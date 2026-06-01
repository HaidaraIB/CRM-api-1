"""PBX webhook and connector endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.decorators import rate_limit_webhook
from integrations.models import (
    PbxCallRecord,
    PbxDialCommand,
    PbxDialCommandStatus,
    PbxSettings,
    UserPbxExtension,
)
from integrations.views.twilio_sms import _integration_gate
from integrations.services.pbx_connector_package import build_connector_zip
from integrations.serializers_pbx import (
    PbxDialCommandSerializer,
    PbxDialRequestSerializer,
    PbxSettingsSerializer,
    UserPbxExtensionSerializer,
)
from integrations.services.pbx_handler import process_pbx_payload

logger = logging.getLogger(__name__)


def _verify_webhook_signature(request, settings: PbxSettings) -> bool:
    secret = (settings.webhook_secret or "").strip()
    if not secret:
        return True
    sig = request.headers.get("X-PBX-Signature", "")
    if not sig:
        return False
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
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-Connector-Key") or "").strip()


def _ensure_pbx_settings(company) -> PbxSettings:
    settings, _ = PbxSettings.objects.get_or_create(
        company=company,
        defaults={
            "webhook_token": secrets.token_urlsafe(32),
            "connector_api_key": secrets.token_urlsafe(32),
            "connector_install_key": secrets.token_urlsafe(16),
            "webhook_secret": secrets.token_urlsafe(24),
        },
    )
    return settings


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit_webhook(max_requests=200, window=60)
def pbx_webhook(request, webhook_token: str):
    settings = _get_settings_by_webhook_token(webhook_token)
    if not settings:
        return HttpResponse(status=404)
    if not _verify_webhook_signature(request, settings):
        return HttpResponse(status=403)
    try:
        result = process_pbx_payload(
            settings,
            request.body,
            request.content_type or "",
        )
        return JsonResponse(result)
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
@permission_classes([IsAuthenticated, HasActiveSubscription])
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
    obj, _ = UserPbxExtension.objects.update_or_create(
        user=serializer.validated_data["user"],
        defaults={
            "company": company,
            "extension": serializer.validated_data["extension"],
        },
    )
    return success_response(UserPbxExtensionSerializer(obj).data, status_code=201)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_extension_delete_view(request, pk: int):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate
    deleted, _ = UserPbxExtension.objects.filter(company=company, pk=pk).delete()
    if not deleted:
        return error_response("Not found.", status_code=404)
    return success_response({"deleted": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def pbx_dial_view(request):
    company = request.user.company
    gate = _integration_gate(company, "pbx")
    if gate:
        return gate

    settings = _ensure_pbx_settings(company)
    if not settings.is_enabled:
        return error_response("PBX integration is not enabled.", status_code=400)

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

    checks = {
        "integration_enabled": bool(settings.is_enabled),
        "pbx_host_configured": pbx_host_configured,
        "ami_configured": ami_configured,
        "connector_online": bool(serialized.get("connector_online")),
        "extensions_mapped": extensions_count > 0,
        "events_received": last_event_at is not None,
    }

    return success_response(
        {
            "is_enabled": settings.is_enabled,
            "connector_online": serialized.get("connector_online"),
            "connector_last_seen_at": settings.connector_last_seen_at,
            "last_event_at": last_event_at,
            "extensions_mapped_count": extensions_count,
            "pbx_host_configured": pbx_host_configured,
            "ami_configured": ami_configured,
            "pbx_host": settings.pbx_host or "",
            "push_event_url_hint": "http://<connector-pc-ip>:8787",
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
            )
            return success_response(result)
        except Exception:
            logger.exception("Connector event failed")
            return error_response("Processing failed.", status_code=500)

    results = []
    for ev in events:
        body = json.dumps(ev).encode("utf-8") if isinstance(ev, dict) else str(ev).encode("utf-8")
        try:
            results.append(process_pbx_payload(settings, body, "application/json"))
        except Exception as exc:
            results.append({"ok": False, "error": str(exc)})
    return success_response({"results": results})


@api_view(["GET"])
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
