"""PBX webhook and connector endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

from django.core import signing

from django.db import OperationalError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from accounts.permissions import HasActiveSubscription, IsAdmin, IsAdminOrReadOnlyForEmployee
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.decorators import rate_limit_webhook
from integrations.models import (
    PbxCallRecord,
    PbxDialCommand,
    PbxDialCommandStatus,
    PbxRecordingStatus,
    PbxSettings,
    SoftphonePlatform,
    UserPbxExtension,
    UserSoftphoneDevice,
)
from integrations.services.pbx_recording_service import (
    finalize_recording_upload,
    list_pending_recording_jobs,
    mark_recording_failed,
    stream_recording_for_user,
    verify_playback_token,
)
from integrations.views.twilio_sms import _integration_gate
from integrations.services.pbx_connector_package import build_connector_zip
from integrations.serializers_pbx import (
    PbxDialCommandSerializer,
    PbxDialRequestSerializer,
    PbxSettingsSerializer,
    SoftphoneDeviceSerializer,
    UserPbxExtensionSerializer,
)
from integrations.services.softphone_config import (
    build_softphone_config,
    user_softphone_ready,
)
from integrations.services.softphone_offboarding import offboard_softphone_user
from integrations.services.pbx_handler import log_incoming_zycoo_push, process_pbx_payload
from integrations.pbx_connector_meta import get_pbx_connector_version

logger = logging.getLogger(__name__)


def _verify_webhook_signature(request, settings: PbxSettings) -> bool:
    """
    Optional HMAC verification via X-PBX-Signature.

    ZYCOO Push Event does not send this header. The webhook URL already contains
    an unguessable token; we only verify HMAC when the caller supplies a signature.
    """
    secret = (settings.webhook_secret or "").strip()
    sig = (request.headers.get("X-PBX-Signature") or "").strip()
    if not secret or not sig:
        return True
    expected = hmac.new(
        secret.encode("utf-8"),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _get_settings_by_webhook_token(token: str) -> PbxSettings | None:
    try:
        return PbxSettings.objects.select_related("company").get(webhook_token=token)
    except PbxSettings.DoesNotExist:
        return None


def _get_settings_by_connector_key(key: str) -> PbxSettings | None:
    if not key:
        return None
    try:
        return PbxSettings.objects.select_related("company").get(connector_api_key=key)
    except PbxSettings.DoesNotExist:
        return None


def _extract_connector_key(request) -> str:
    key = (request.headers.get("X-Connector-Key") or "").strip()
    if key:
        return key
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _ensure_pbx_settings(company) -> PbxSettings:
    settings, _ = PbxSettings.objects.get_or_create(
        company=company,
        defaults={
            "webhook_token": secrets.token_urlsafe(32),
            "connector_api_key": secrets.token_urlsafe(32),
            "connector_install_key": secrets.token_urlsafe(16),
            # Empty: ZYCOO cannot send X-PBX-Signature; URL token is sufficient.
            "webhook_secret": "",
        },
    )
    return settings


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit_webhook(max_requests=200, window=60)
def pbx_webhook(request, webhook_token: str):
    settings = _get_settings_by_webhook_token(webhook_token)
    if not settings:
        log_incoming_zycoo_push(
            company_id=None,
            source="webhook",
            raw_body=request.body,
            content_type=request.content_type or "",
            webhook_token_prefix=webhook_token,
        )
        logger.warning("ZYCOO webhook unknown token prefix=%s", (webhook_token or "")[:12])
        return HttpResponse(status=404)
    if not _verify_webhook_signature(request, settings):
        log_incoming_zycoo_push(
            company_id=settings.company_id,
            source="webhook",
            raw_body=request.body,
            content_type=request.content_type or "",
            webhook_token_prefix=webhook_token,
        )
        logger.warning("ZYCOO webhook signature rejected company_id=%s", settings.company_id)
        return HttpResponse(status=403)
    try:
        result = process_pbx_payload(
            settings,
            request.body,
            request.content_type or "",
            source="webhook",
            webhook_token_prefix=webhook_token,
        )
        return JsonResponse(result)
    except OperationalError as exc:
        if "locked" in str(exc).lower():
            logger.exception("PBX webhook database locked after retries")
            # Acknowledge so ZYCOO does not retry-storm; event may need manual replay.
            return JsonResponse({"ok": False, "reason": "database_busy"}, status=200)
        raise
    except Exception:
        logger.exception("PBX webhook processing failed")
        return JsonResponse({"ok": False, "reason": "processing_error"}, status=500)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_settings_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    settings = _ensure_pbx_settings(company)

    if request.method == "GET":
        return success_response(PbxSettingsSerializer(settings, context={"request": request}).data)

    serializer = PbxSettingsSerializer(
        settings,
        data=request.data,
        partial=True,
        context={"request": request},
    )
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)
    serializer.save()
    return success_response(PbxSettingsSerializer(settings, context={"request": request}).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_rotate_connector_key_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate
    settings = _ensure_pbx_settings(company)
    settings.connector_api_key = secrets.token_urlsafe(32)
    settings.save(update_fields=["connector_api_key", "updated_at"])
    return success_response({"connector_api_key": settings.connector_api_key})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription, IsAdminOrReadOnlyForEmployee])
def pbx_extensions_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    if request.method == "GET":
        qs = UserPbxExtension.objects.filter(company=company).select_related("user")
        return success_response(UserPbxExtensionSerializer(qs, many=True).data)

    serializer = UserPbxExtensionSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    user = serializer.validated_data["user"]
    defaults = {
        "company": company,
        "extension": serializer.validated_data["extension"],
        "softphone_enabled": serializer.validated_data.get("softphone_enabled", True),
    }
    obj, created = UserPbxExtension.objects.update_or_create(
        user=user,
        defaults=defaults,
    )
    sip_password = request.data.get("sip_password")
    if sip_password:
        from integrations.encryption import encrypt_token

        obj.sip_password = encrypt_token(str(sip_password))
        obj.save(update_fields=["sip_password", "updated_at"])
    return success_response(
        UserPbxExtensionSerializer(obj).data,
        status_code=201 if created else 200,
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, HasActiveSubscription, IsAdmin])
def pbx_extension_detail_view(request, pk: int):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    try:
        obj = UserPbxExtension.objects.select_related("user").get(company=company, pk=pk)
    except UserPbxExtension.DoesNotExist:
        return error_response("Not found.", status_code=404)

    if request.method == "DELETE":
        offboard_softphone_user(obj.user, clear_sip_password=True, mapping=obj)
        obj.delete()
        return success_response({"deleted": True})

    prev_user_id = obj.user_id
    prev_softphone_enabled = obj.softphone_enabled
    serializer = UserPbxExtensionSerializer(
        obj,
        data=request.data,
        partial=True,
        context={"request": request},
    )
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)
    obj = serializer.save()
    if prev_user_id != obj.user_id:
        from accounts.models import User

        try:
            prev_user = User.objects.get(pk=prev_user_id)
            offboard_softphone_user(prev_user, clear_sip_password=True)
        except User.DoesNotExist:
            pass
    if prev_softphone_enabled and not obj.softphone_enabled:
        offboard_softphone_user(obj.user, mapping=obj)
    return success_response(UserPbxExtensionSerializer(obj).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_dial_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    settings = _ensure_pbx_settings(company)
    if not settings.is_enabled:
        return error_response(
            "PBX integration is not enabled.",
            code="pbx_not_enabled",
            status_code=400,
        )

    serializer = PbxDialRequestSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    client = serializer.validated_data["client"]
    phone_number = serializer.validated_data["phone_number"]
    extension = serializer.validated_data.get("extension")
    if not extension:
        try:
            extension = request.user.pbx_extension.extension
        except UserPbxExtension.DoesNotExist:
            return error_response(
                "No PBX extension mapped for your user.",
                code="no_extension",
                status_code=400,
            )

    cmd = PbxDialCommand.objects.create(
        company=company,
        requested_by=request.user,
        client=client,
        phone_number=phone_number,
        extension=extension,
        status=PbxDialCommandStatus.PENDING,
    )
    return success_response(PbxDialCommandSerializer(cmd).data, status_code=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_dial_status_view(request, command_id: int):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate
    try:
        cmd = PbxDialCommand.objects.get(company=company, pk=command_id)
    except PbxDialCommand.DoesNotExist:
        return error_response("Not found.", status_code=404)
    return success_response(PbxDialCommandSerializer(cmd).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_health_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    settings = _ensure_pbx_settings(company)
    serialized = PbxSettingsSerializer(settings, context={"request": request}).data
    extensions_count = UserPbxExtension.objects.filter(company=company).count()
    last_event_at = (
        PbxCallRecord.objects.filter(company=company)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )

    pbx_host_configured = bool((settings.pbx_host or "").strip())
    ami_configured = bool((settings.ami_username or "").strip())
    webhook_url = serialized.get("webhook_url") or ""

    recording_pending = PbxCallRecord.objects.filter(
        company=company,
        recording_status__in=(
            PbxRecordingStatus.PENDING,
            PbxRecordingStatus.PROCESSING,
        ),
    ).count()
    recording_failed = PbxCallRecord.objects.filter(
        company=company,
        recording_status=PbxRecordingStatus.FAILED,
    ).count()
    last_recording_ready_at = (
        PbxCallRecord.objects.filter(
            company=company,
            recording_status=PbxRecordingStatus.READY,
        )
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )

    checks = {
        "integration_enabled": bool(settings.is_enabled),
        "pbx_host_configured": pbx_host_configured,
        "ami_configured": ami_configured,
        "connector_online": bool(serialized.get("connector_online")),
        "extensions_mapped": extensions_count > 0,
        "events_received": last_event_at is not None,
        "recordings_clear": recording_pending == 0 and recording_failed == 0,
    }

    return success_response(
        {
            "is_enabled": settings.is_enabled,
            "connector_online": serialized.get("connector_online"),
            "connector_last_seen_at": settings.connector_last_seen_at,
            "connector_package_version": get_pbx_connector_version(),
            "last_event_at": last_event_at,
            "extensions_mapped_count": extensions_count,
            "pbx_host_configured": pbx_host_configured,
            "ami_configured": ami_configured,
            "pbx_host": settings.pbx_host or "",
            "webhook_url": webhook_url,
            "push_event_url_hint": webhook_url or "http://<connector-pc-ip>:8787",
            "push_event_connector_hint": "http://<connector-pc-ip>:8787",
            "recordings": {
                "pending": recording_pending,
                "failed": recording_failed,
                "last_ready_at": last_recording_ready_at,
            },
            "checks": checks,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_connector_download_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    settings = _ensure_pbx_settings(company)
    payload = build_connector_zip(settings, request)
    response = HttpResponse(payload, content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="loop-pbx-connector.zip"'
    return response


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def pbx_connector_heartbeat_view(request):
    settings = _get_settings_by_connector_key(_extract_connector_key(request))
    if not settings:
        return error_response(
            "Invalid connector API key.",
            code="invalid_connector_key",
            status_code=401,
        )
    settings.connector_last_seen_at = timezone.now()
    settings.save(update_fields=["connector_last_seen_at", "updated_at"])
    return success_response({"ok": True})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def pbx_connector_events_view(request):
    settings = _get_settings_by_connector_key(_extract_connector_key(request))
    if not settings:
        return error_response(
            "Invalid connector API key.",
            code="invalid_connector_key",
            status_code=401,
        )

    events = request.data.get("events") if isinstance(request.data, dict) else None
    if events is None:
        # single event as raw body
        try:
            result = process_pbx_payload(
                settings,
                request.body,
                request.content_type or "",
                source="connector",
            )
            return success_response(result)
        except Exception:
            logger.exception("Connector event failed")
            return error_response("Processing failed.", status_code=500)

    results = []
    for ev in events:
        body = json.dumps(ev).encode("utf-8") if isinstance(ev, dict) else str(ev).encode("utf-8")
        try:
            results.append(
                process_pbx_payload(settings, body, "application/json", source="connector")
            )
        except Exception as exc:
            results.append({"ok": False, "error": str(exc)})
    return success_response({"results": results})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def pbx_connector_commands_view(request):
    settings = _get_settings_by_connector_key(_extract_connector_key(request))
    if not settings:
        return error_response(
            "Invalid connector API key.",
            code="invalid_connector_key",
            status_code=401,
        )

    settings.connector_last_seen_at = timezone.now()
    settings.save(update_fields=["connector_last_seen_at", "updated_at"])

    pending = (
        PbxDialCommand.objects.filter(
            company=settings.company,
            status=PbxDialCommandStatus.PENDING,
        )
        .order_by("created_at")[:10]
    )
    data = []
    for cmd in pending:
        cmd.status = PbxDialCommandStatus.PROCESSING
        cmd.save(update_fields=["status"])
        data.append(
            {
                "id": cmd.id,
                "phone_number": cmd.phone_number,
                "extension": cmd.extension,
            }
        )
    return success_response({"commands": data})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def pbx_connector_command_ack_view(request, command_id: int):
    settings = _get_settings_by_connector_key(_extract_connector_key(request))
    if not settings:
        return error_response(
            "Invalid connector API key.",
            code="invalid_connector_key",
            status_code=401,
        )

    try:
        cmd = PbxDialCommand.objects.get(
            company=settings.company,
            pk=command_id,
        )
    except PbxDialCommand.DoesNotExist:
        return error_response("Not found.", status_code=404)

    success = bool(request.data.get("success", True))
    cmd.status = PbxDialCommandStatus.COMPLETED if success else PbxDialCommandStatus.FAILED
    cmd.result_message = (request.data.get("message") or "")[:2000]
    cmd.processed_at = timezone.now()
    cmd.save(update_fields=["status", "result_message", "processed_at"])
    return success_response({"ok": True})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def pbx_connector_recording_jobs_view(request):
    settings = _get_settings_by_connector_key(_extract_connector_key(request))
    if not settings:
        return error_response(
            "Invalid connector API key.",
            code="invalid_connector_key",
            status_code=401,
        )
    settings.connector_last_seen_at = timezone.now()
    settings.save(update_fields=["connector_last_seen_at", "updated_at"])
    jobs = list_pending_recording_jobs(settings.company_id)
    return success_response({"jobs": jobs})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def pbx_connector_recording_upload_view(request, record_id: int):
    settings = _get_settings_by_connector_key(_extract_connector_key(request))
    if not settings:
        return error_response(
            "Invalid connector API key.",
            code="invalid_connector_key",
            status_code=401,
        )
    try:
        record = PbxCallRecord.objects.get(pk=record_id, company=settings.company)
    except PbxCallRecord.DoesNotExist:
        return error_response("Not found.", status_code=404)

    upload = request.FILES.get("file")
    if not upload:
        mark_recording_failed(record_id, company_id=settings.company_id)
        return error_response("Missing file.", code="missing_file", status_code=400)

    try:
        finalize_recording_upload(
            record_id=record.id,
            company_id=settings.company_id,
            file_bytes=upload.read(),
            original_filename=upload.name or "recording.wav",
        )
    except Exception:
        logger.exception("Recording upload failed record_id=%s", record_id)
        mark_recording_failed(record_id, company_id=settings.company_id)
        return error_response("Upload failed.", status_code=500)

    return success_response({"ok": True, "record_id": record.id, "status": "ready"})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def pbx_recording_play_view(request, record_id: int):
    token = (request.query_params.get("token") or "").strip()
    company_id = None
    if token:
        try:
            rid, company_id = verify_playback_token(token)
            if rid != record_id:
                return error_response("Invalid token.", code="invalid_token", status_code=403)
        except (signing.BadSignature, signing.SignatureExpired):
            return error_response("Invalid or expired token.", code="invalid_token", status_code=403)
    elif request.user.is_authenticated and getattr(request.user, "company_id", None):
        company_id = request.user.company_id
    else:
        return error_response("Authentication required.", status_code=401)

    try:
        record = PbxCallRecord.objects.get(pk=record_id, company_id=company_id)
    except PbxCallRecord.DoesNotExist:
        raise Http404 from None

    if record.recording_status != PbxRecordingStatus.READY:
        return error_response("Recording not ready.", code="not_ready", status_code=404)

    try:
        blob = stream_recording_for_user(record)
    except FileNotFoundError:
        return error_response("Recording file missing.", code="missing_file", status_code=404)

    filename = record.recording_path.split("/")[-1] if record.recording_path else "recording.wav"
    response = FileResponse(blob, content_type="audio/wav")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_reports_summary_view(request):
    from django.db.models import Avg, Count, Q
    from integrations.models import PbxCallRecord, PbxCallDisposition, PbxCallDirection

    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    qs = PbxCallRecord.objects.filter(company=company, event_type="hangup")
    date_from = request.query_params.get("from")
    date_to = request.query_params.get("to")
    if date_from:
        qs = qs.filter(started_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(started_at__date__lte=date_to)

    total = qs.count()
    inbound = qs.filter(direction=PbxCallDirection.INBOUND).count()
    outbound = qs.filter(direction=PbxCallDirection.OUTBOUND).count()
    answered = qs.filter(disposition=PbxCallDisposition.ANSWERED).count()
    missed = qs.filter(
        disposition__in=[PbxCallDisposition.NO_ANSWER, PbxCallDisposition.BUSY]
    ).count()
    avg_duration = qs.aggregate(avg=Avg("billsec"))["avg"] or 0

    return success_response(
        {
            "total": total,
            "inbound": inbound,
            "outbound": outbound,
            "answered": answered,
            "missed": missed,
            "avg_duration_sec": round(avg_duration, 1),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_reports_agents_view(request):
    from django.db.models import Avg
    from integrations.models import PbxCallRecord, PbxCallDisposition

    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    qs = PbxCallRecord.objects.filter(company=company, event_type="hangup")
    date_from = request.query_params.get("from")
    date_to = request.query_params.get("to")
    if date_from:
        qs = qs.filter(started_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(started_at__date__lte=date_to)

    agents = []
    for ext in qs.values_list("extension", flat=True).distinct():
        if not ext:
            continue
        ext_qs = qs.filter(extension=ext)
        mapping = (
            UserPbxExtension.objects.filter(company=company, extension=ext)
            .select_related("user")
            .first()
        )
        agents.append(
            {
                "extension": ext,
                "user_id": mapping.user_id if mapping else None,
                "username": mapping.user.username if mapping else None,
                "total": ext_qs.count(),
                "answered": ext_qs.filter(disposition=PbxCallDisposition.ANSWERED).count(),
                "missed": ext_qs.filter(
                    disposition__in=[PbxCallDisposition.NO_ANSWER, PbxCallDisposition.BUSY]
                ).count(),
                "avg_duration_sec": round(ext_qs.aggregate(avg=Avg("billsec"))["avg"] or 0, 1),
            }
        )
    return success_response({"agents": agents})


def _get_user_pbx_extension(user) -> UserPbxExtension | None:
    try:
        return UserPbxExtension.objects.select_related("user").get(user=user)
    except UserPbxExtension.DoesNotExist:
        return None


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_softphone_config_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    settings = _ensure_pbx_settings(company)
    mapping = _get_user_pbx_extension(request.user)
    if mapping is None:
        return error_response(
            "No PBX extension mapped for your user.",
            code="no_extension",
            status_code=400,
        )
    if not user_softphone_ready(settings, mapping):
        return error_response(
            "Softphone is not configured for your user.",
            code="softphone_not_configured",
            status_code=400,
        )

    platform = (request.query_params.get("platform") or "web").lower()
    if platform not in SoftphonePlatform.values:
        platform = SoftphonePlatform.WEB

    config = build_softphone_config(settings, mapping, platform=platform)
    config["softphone_enabled"] = True
    config["expires_in"] = 300
    return success_response(config)


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_softphone_devices_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    settings = _ensure_pbx_settings(company)
    if not settings.softphone_enabled:
        return error_response(
            "Softphone is not enabled for your company.",
            code="softphone_disabled",
            status_code=400,
        )

    if request.method == "DELETE":
        platform = (request.query_params.get("platform") or "").lower()
        device_id = (request.query_params.get("device_id") or "").strip()
        qs = UserSoftphoneDevice.objects.filter(user=request.user, company=company)
        if platform:
            qs = qs.filter(platform=platform)
        if device_id:
            qs = qs.filter(device_id=device_id)
        deleted, _ = qs.delete()
        return success_response({"deleted": deleted})

    serializer = SoftphoneDeviceSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    data = serializer.validated_data
    device, _ = UserSoftphoneDevice.objects.update_or_create(
        user=request.user,
        platform=data["platform"],
        device_id=data.get("device_id") or "",
        defaults={
            "company": company,
            "fcm_token": data.get("fcm_token") or "",
            "voip_token": data.get("voip_token") or "",
        },
    )
    return success_response(
        {
            "id": device.id,
            "platform": device.platform,
            "device_id": device.device_id,
            "last_registered_at": device.last_registered_at,
        },
        status_code=201,
    )
