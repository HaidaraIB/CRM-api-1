"""WhatsApp Cloud Calling REST endpoints (web CRM)."""

from __future__ import annotations

import logging
import mimetypes

from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.services.whatsapp_call_error_logs import log_whatsapp_call_error
from integrations.models import (
    MessageTemplate,
    WhatsAppAccount,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallErrorSource,
    WhatsAppCallRecordingStatus,
    WhatsAppCallStatus,
)
from integrations.services.whatsapp_call_availability import (
    normalize_weekly_schedule,
    serialize_agent_call_status,
    serialize_call_hours,
    set_agent_call_away,
    sync_call_hours_to_meta,
    user_is_whatsapp_call_away,
)
from integrations.services.whatsapp_calling import (
    WhatsAppCallingError,
    call_permission_allows_start,
    enable_calling_on_account,
    ensure_client_call_for_whatsapp_call,
    find_call_permission_template,
    get_call_permissions,
    get_wa_playback_url,
    graph_call_action,
    mark_call_answered,
    mark_call_rejected,
    send_call_permission_request,
    send_call_permission_request_interactive,
    store_call_recording,
    stream_wa_recording,
    verify_wa_playback_token,
)
from integrations.whatsapp_access import (
    user_can_access_client,
    user_can_access_whatsapp_calls,
    user_sees_all_company_leads,
)
from integrations.whatsapp_account_sync import resolve_whatsapp_account_for_api
from integrations.views.webhooks_messaging import _integration_gate

logger = logging.getLogger(__name__)


def _calling_error_code_and_message(exc: WhatsAppCallingError) -> tuple[str, str, dict | None]:
    graph_status = exc.status_code or 400
    body = exc.body if isinstance(exc.body, dict) else {}
    nested = body.get("error") if isinstance(body.get("error"), dict) else {}
    meta_code = nested.get("code")
    meta_msg = (nested.get("message") or str(exc) or "").strip()

    if graph_status in (401, 403):
        return (
            "whatsapp_token_invalid",
            meta_msg
            or "WhatsApp access token is invalid. Reconnect WhatsApp in Integrations.",
            body or None,
        )
    if meta_code == 141000:
        return (
            "whatsapp_not_cloud_api_number",
            meta_msg
            or "This WhatsApp number is not eligible for Cloud Calling "
            "(often a coexistence / Business-app number, or not fully Cloud-API registered).",
            body or None,
        )
    if meta_code == 138015:
        return (
            "whatsapp_calling_not_eligible",
            meta_msg
            or "Calling cannot be enabled for this phone number (messaging limit / eligibility).",
            body or None,
        )
    if meta_code == 138000:
        return (
            "whatsapp_calling_disabled",
            meta_msg or "Calling is not enabled for this phone number.",
            body or None,
        )
    return (
        "whatsapp_calling_error",
        meta_msg or "WhatsApp calling request failed",
        body or None,
    )


def _calling_error_response(
    exc: WhatsAppCallingError,
    *,
    log_ctx: dict | None = None,
):
    """
    Map Graph API failures to CRM client responses.

    Meta auth failures arrive as HTTP 401/403 — returning those to the SPA would
    trigger JWT refresh / session wipe. Remap them to 502 with an explicit code.
    """
    code, message, details = _calling_error_code_and_message(exc)
    graph_status = exc.status_code or 400

    if log_ctx:
        log_whatsapp_call_error(
            company=log_ctx.get("company"),
            source=log_ctx.get("source") or WhatsAppCallErrorSource.INITIATE.value,
            error_code=code,
            error_message=message,
            agent=log_ctx.get("agent"),
            client=log_ctx.get("client"),
            peer_phone=log_ctx.get("peer_phone") or "",
            whatsapp_account=log_ctx.get("whatsapp_account"),
            whatsapp_call=log_ctx.get("whatsapp_call"),
            meta_details=details if isinstance(details, dict) else {},
        )

    if code == "whatsapp_token_invalid":
        return error_response(message, code=code, details=exc.body, status_code=502)
    if code in (
        "whatsapp_not_cloud_api_number",
        "whatsapp_calling_not_eligible",
        "whatsapp_calling_disabled",
    ):
        return error_response(message, code=code, details=exc.body, status_code=400)

    client_status = graph_status if 400 <= graph_status < 500 else 502
    return error_response(
        message,
        code=code,
        details=exc.body,
        status_code=client_status,
    )


def _serialize_call(call: WhatsAppCall, request=None) -> dict:
    client = call.client
    return {
        "id": call.id,
        "meta_call_id": call.meta_call_id,
        "direction": call.direction,
        "status": call.status,
        "peer_phone": call.peer_phone,
        "peer_name": call.peer_name,
        "client": client.id if client else None,
        "client_name": client.name if client else None,
        "client_stage": getattr(getattr(client, "status", None), "name", None) if client else None,
        "agent": call.agent_id,
        "agent_username": getattr(call.agent, "username", None) if call.agent_id else None,
        "whatsapp_account_id": call.whatsapp_account_id,
        "offer_sdp": call.offer_sdp or None,
        "answer_sdp": call.answer_sdp or None,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "answered_at": call.answered_at.isoformat() if call.answered_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "duration_sec": call.duration_sec,
        "notes": call.notes or "",
        "recording_status": call.recording_status,
        "recording_url": get_wa_playback_url(call, request),
        "client_call": call.client_call_id,
        "error_message": call.error_message or "",
        "created_at": call.created_at.isoformat() if call.created_at else None,
        "updated_at": call.updated_at.isoformat() if call.updated_at else None,
    }


def _company_calls_qs(user):
    qs = WhatsAppCall.objects.filter(company=user.company).select_related(
        "client",
        "client__status",
        "agent",
        "whatsapp_account",
    )
    if user_sees_all_company_leads(user):
        return qs
    # Staff: only assigned leads + unassigned ringing (so they can pick up unknown callers
    # only if policy allows — for MVP, staff only see calls for their clients OR
    # ringing inbound with no client yet assigned to anyone).
    from django.db.models import Q

    return qs.filter(
        Q(client__assigned_to_id=user.id)
        | Q(client__isnull=True, status=WhatsAppCallStatus.RINGING, agent__isnull=True)
        | Q(agent_id=user.id)
    )


def _get_call_for_user(user, call_id: int) -> WhatsAppCall | None:
    return _company_calls_qs(user).filter(pk=call_id).first()


def _resolve_account(user, account_id=None) -> tuple[WhatsAppAccount | None, str | None]:
    company = user.company
    if account_id:
        wa = WhatsAppAccount.objects.filter(
            company=company, pk=account_id, status="connected"
        ).first()
        if not wa:
            return None, "whatsapp_account_not_found"
        return wa, None
    wa, err = resolve_whatsapp_account_for_api(company)
    return wa, err


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_calls_list(request):
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)

    qs = _company_calls_qs(request.user)
    status_filter = (request.query_params.get("status") or "").strip()
    direction = (request.query_params.get("direction") or "").strip()
    my_calls = (request.query_params.get("my_calls") or "").lower() in ("1", "true", "yes")
    has_recording = (request.query_params.get("has_recording") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    search = (request.query_params.get("search") or "").strip()
    client_id = request.query_params.get("client")
    ordering = (request.query_params.get("ordering") or "-created_at").strip()

    # Non-status filters first so sidebar counters match the filtered set.
    if direction in (WhatsAppCallDirection.INBOUND, WhatsAppCallDirection.OUTBOUND):
        qs = qs.filter(direction=direction)
    if my_calls:
        qs = qs.filter(agent_id=request.user.id)
    agent_id = (request.query_params.get("agent") or "").strip()
    if agent_id and not my_calls:
        if not user_sees_all_company_leads(request.user):
            return error_response("Not allowed to filter by agent", status_code=403)
        try:
            qs = qs.filter(agent_id=int(agent_id))
        except (TypeError, ValueError):
            return validation_error_response({"agent": ["Invalid agent id"]})
    if has_recording:
        qs = qs.filter(recording_status=WhatsAppCallRecordingStatus.READY)
    if client_id:
        try:
            qs = qs.filter(client_id=int(client_id))
        except (TypeError, ValueError):
            return validation_error_response({"client": ["Invalid client id"]})
    if search:
        from django.db.models import Q

        qs = qs.filter(
            Q(peer_phone__icontains=search)
            | Q(peer_name__icontains=search)
            | Q(client__name__icontains=search)
        )

    status_counts = {
        s: qs.filter(status=s).count()
        for s in (
            WhatsAppCallStatus.RINGING,
            WhatsAppCallStatus.ANSWERED,
            WhatsAppCallStatus.ENDED,
            WhatsAppCallStatus.MISSED,
            WhatsAppCallStatus.NO_ANSWER,
            WhatsAppCallStatus.REJECTED,
            WhatsAppCallStatus.FAILED,
        )
    }

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        qs = qs.filter(status__in=statuses)

    if ordering.lstrip("-") not in (
        "created_at",
        "started_at",
        "duration_sec",
        "status",
    ):
        ordering = "-created_at"
    qs = qs.order_by(ordering)

    try:
        limit = min(int(request.query_params.get("limit") or 50), 200)
        offset = max(int(request.query_params.get("offset") or 0), 0)
    except (TypeError, ValueError):
        limit, offset = 50, 0

    total = qs.count()
    page = qs[offset : offset + limit]
    return success_response(
        {
            "count": total,
            "results": [_serialize_call(c, request) for c in page],
            "status_counts": status_counts,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_calls_pending(request):
    """Ringing inbound calls for the agent to answer (poll ~1s)."""
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)

    agent_away = user_is_whatsapp_call_away(request.user)
    results = []
    seen = set()

    if not agent_away:
        # Only ring the assigned agent (or, for an unassigned lead, anyone eligible
        # to pick it up) — company-wide viewers (owner/admin/reception/etc.) must
        # not be rung for calls on leads assigned to someone else.
        from django.db.models import Q

        qs = (
            _company_calls_qs(request.user)
            .filter(
                status=WhatsAppCallStatus.RINGING,
                direction=WhatsAppCallDirection.INBOUND,
                agent__isnull=True,
            )
            .filter(Q(client__isnull=True) | Q(client__assigned_to_id=request.user.id))
            .order_by("created_at")[:20]
        )
        for call in qs:
            if call.id in seen:
                continue
            seen.add(call.id)
            results.append(_serialize_call(call, request))

    # Outbound ringing owned by this agent (waiting for remote SDP answer)
    mine = (
        _company_calls_qs(request.user)
        .filter(
            status=WhatsAppCallStatus.RINGING,
            agent_id=request.user.id,
        )
        .order_by("-created_at")[:10]
    )
    for call in mine:
        if call.id in seen:
            continue
        seen.add(call.id)
        results.append(_serialize_call(call, request))
    return success_response(
        {
            "results": results,
            "agent_status": serialize_agent_call_status(request.user),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_calls_live(request):
    """
    Live calls for the Calls page: ringing (answerable) + answered in-progress.
    Away agents do not receive unassigned ringing rows.

    Stale rows (missed terminate webhooks) are closed so LIVE CALLS does not
    show forever-running timers.
    """
    from datetime import timedelta

    from django.db.models import Q

    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)

    now = timezone.now()
    answered_cutoff = now - timedelta(hours=3)
    ringing_cutoff = now - timedelta(minutes=5)

    company_id = request.user.company_id
    if company_id:
        stale_answered = WhatsAppCall.objects.filter(
            company_id=company_id,
            status=WhatsAppCallStatus.ANSWERED,
        ).filter(
            Q(answered_at__lt=answered_cutoff)
            | Q(answered_at__isnull=True, updated_at__lt=answered_cutoff)
        )[:25]
        for call in stale_answered:
            call.status = WhatsAppCallStatus.ENDED
            if not call.ended_at:
                call.ended_at = now
            if call.answered_at and call.ended_at:
                call.duration_sec = max(
                    0, int((call.ended_at - call.answered_at).total_seconds())
                )
            call.save(
                update_fields=["status", "ended_at", "duration_sec", "updated_at"]
            )

        stale_ringing = WhatsAppCall.objects.filter(
            company_id=company_id,
            status=WhatsAppCallStatus.RINGING,
            created_at__lt=ringing_cutoff,
        )[:25]
        for call in stale_ringing:
            if call.direction == WhatsAppCallDirection.INBOUND and not call.answered_at:
                call.status = WhatsAppCallStatus.MISSED
            elif call.direction == WhatsAppCallDirection.OUTBOUND and not call.answered_at:
                call.status = WhatsAppCallStatus.NO_ANSWER
            else:
                call.status = WhatsAppCallStatus.ENDED
            if not call.ended_at:
                call.ended_at = now
            call.save(update_fields=["status", "ended_at", "updated_at"])

    # RINGING must only reach the assigned agent (or an unassigned inbound call
    # nobody has claimed yet) — never every company-wide viewer, or owners/admins
    # get rung for leads that aren't theirs. ANSWERED stays company-wide so
    # supervisors can still monitor in-progress calls on the Live Calls page.
    personal_scope = (
        Q(client__assigned_to_id=request.user.id)
        | Q(client__isnull=True, agent__isnull=True)
        | Q(agent_id=request.user.id)
    )
    qs = _company_calls_qs(request.user).filter(
        Q(
            status=WhatsAppCallStatus.RINGING,
            created_at__gte=ringing_cutoff,
        )
        & personal_scope
        | Q(status=WhatsAppCallStatus.ANSWERED, answered_at__gte=answered_cutoff)
        | Q(
            status=WhatsAppCallStatus.ANSWERED,
            answered_at__isnull=True,
            updated_at__gte=answered_cutoff,
        )
    )
    agent_away = user_is_whatsapp_call_away(request.user)
    results = []
    for call in qs.order_by("-updated_at")[:50]:
        if (
            agent_away
            and call.status == WhatsAppCallStatus.RINGING
            and call.direction == WhatsAppCallDirection.INBOUND
            and call.agent_id is None
        ):
            continue
        results.append(_serialize_call(call, request))
    return success_response(
        {
            "results": results,
            "count": len(results),
            "agent_status": serialize_agent_call_status(request.user),
        }
    )

@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_detail(request, pk: int):
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    call = _get_call_for_user(request.user, pk)
    if not call:
        return error_response("Call not found", status_code=404)
    return success_response(_serialize_call(call, request))


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_pre_accept(request, pk: int):
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    call = _get_call_for_user(request.user, pk)
    if not call:
        return error_response("Call not found", status_code=404)
    sdp = (request.data.get("sdp") or "").strip()
    if not sdp:
        return validation_error_response({"sdp": ["Required"]})
    try:
        graph_call_action(
            call.whatsapp_account,
            action="pre_accept",
            call_id=call.meta_call_id,
            sdp=sdp,
            sdp_type="answer",
        )
    except WhatsAppCallingError as exc:
        return _calling_error_response(exc)
    call.answer_sdp = sdp
    call.agent = request.user
    call.save(update_fields=["answer_sdp", "agent", "updated_at"])
    return success_response(_serialize_call(call, request))


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_accept(request, pk: int):
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    call = _get_call_for_user(request.user, pk)
    if not call:
        return error_response("Call not found", status_code=404)
    if user_is_whatsapp_call_away(request.user):
        return error_response(
            "You are Away and cannot answer WhatsApp calls",
            code="whatsapp_call_agent_away",
            status_code=400,
        )
    if call.status not in (WhatsAppCallStatus.RINGING, WhatsAppCallStatus.ANSWERED):
        return error_response(f"Cannot accept call in status {call.status}", status_code=400)

    sdp = (request.data.get("sdp") or call.answer_sdp or "").strip()
    if not sdp:
        return validation_error_response({"sdp": ["Required"]})
    try:
        graph_call_action(
            call.whatsapp_account,
            action="accept",
            call_id=call.meta_call_id,
            sdp=sdp,
            sdp_type="answer",
        )
    except WhatsAppCallingError as exc:
        return _calling_error_response(
            exc,
            log_ctx={
                "company": request.user.company,
                "source": WhatsAppCallErrorSource.ACCEPT.value,
                "agent": request.user,
                "client": call.client,
                "peer_phone": call.peer_phone,
                "whatsapp_account": call.whatsapp_account,
                "whatsapp_call": call,
            },
        )
    mark_call_answered(call, agent=request.user, answer_sdp=sdp)
    call.refresh_from_db()
    return success_response(_serialize_call(call, request))


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_reject(request, pk: int):
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    call = _get_call_for_user(request.user, pk)
    if not call:
        return error_response("Call not found", status_code=404)
    try:
        graph_call_action(
            call.whatsapp_account,
            action="reject",
            call_id=call.meta_call_id,
        )
    except WhatsAppCallingError as exc:
        logger.warning("WhatsApp reject Graph error call=%s: %s", call.id, exc)
    mark_call_rejected(call, agent=request.user)
    call.refresh_from_db()
    return success_response(_serialize_call(call, request))


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_terminate(request, pk: int):
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    call = _get_call_for_user(request.user, pk)
    if not call:
        return error_response("Call not found", status_code=404)
    notes = (request.data.get("notes") or "").strip()
    try:
        graph_call_action(
            call.whatsapp_account,
            action="terminate",
            call_id=call.meta_call_id,
        )
    except WhatsAppCallingError as exc:
        logger.warning("WhatsApp terminate Graph error call=%s: %s", call.id, exc)

    updates = ["updated_at"]
    if notes:
        call.notes = notes
        updates.append("notes")
    if not call.ended_at:
        call.ended_at = timezone.now()
        updates.append("ended_at")
    if call.answered_at and call.ended_at:
        call.duration_sec = max(
            0, int((call.ended_at - call.answered_at).total_seconds())
        )
        updates.append("duration_sec")
    if call.status == WhatsAppCallStatus.ANSWERED:
        call.status = WhatsAppCallStatus.ENDED
        updates.append("status")
    elif call.status == WhatsAppCallStatus.RINGING:
        call.status = (
            WhatsAppCallStatus.MISSED
            if call.direction == WhatsAppCallDirection.INBOUND
            else WhatsAppCallStatus.NO_ANSWER
        )
        updates.append("status")
    if not call.answered_at and call.recording_status == WhatsAppCallRecordingStatus.PENDING:
        call.recording_status = WhatsAppCallRecordingStatus.NONE
        updates.append("recording_status")
    call.save(update_fields=list(dict.fromkeys(updates)))
    ensure_client_call_for_whatsapp_call(call)
    call.refresh_from_db()
    return success_response(_serialize_call(call, request))


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_initiate(request):
    """Business-initiated outbound call (requires call permission)."""
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)

    to = (request.data.get("to") or request.data.get("phone") or "").strip()
    sdp = (request.data.get("sdp") or "").strip()
    client_id = request.data.get("client")
    account_id = request.data.get("whatsapp_account_id")
    skip_permission_check = (request.data.get("skip_permission_check") or False) is True

    if not to:
        return validation_error_response({"to": ["Required"]})
    if not sdp:
        return validation_error_response({"to": ["Required"], "sdp": ["Required"]})

    wa, err = _resolve_account(request.user, account_id)
    if not wa:
        return error_response(err or "No connected WhatsApp account", status_code=400)
    if not wa.calling_enabled:
        return error_response(
            "WhatsApp calling is not enabled on this number. Enable it in Integrations → WhatsApp.",
            status_code=400,
            code="whatsapp_calling_disabled",
        )

    from integrations.services.phone_match import find_client_by_phone
    from crm.models import Client

    client = None
    if client_id:
        client = Client.objects.filter(company=request.user.company, pk=client_id).first()
        if not client or not user_can_access_client(request.user, client):
            return error_response("Lead not found", status_code=404)
    else:
        client = find_client_by_phone(request.user.company, to)
        if client and not user_can_access_client(request.user, client):
            return error_response("Lead not found", status_code=404)

    to_digits = "".join(c for c in to if c.isdigit())
    if not skip_permission_check:
        try:
            perms = get_call_permissions(wa, to_digits)
        except WhatsAppCallingError as exc:
            return _calling_error_response(
                exc,
                log_ctx={
                    "company": request.user.company,
                    "source": WhatsAppCallErrorSource.INITIATE.value,
                    "agent": request.user,
                    "client": client,
                    "peer_phone": to_digits,
                    "whatsapp_account": wa,
                },
            )
        if not call_permission_allows_start(perms):
            log_whatsapp_call_error(
                company=request.user.company,
                source=WhatsAppCallErrorSource.INITIATE.value,
                error_code="whatsapp_call_permission_required",
                error_message="Call permission required. Send a call permission request first.",
                agent=request.user,
                client=client,
                peer_phone=to_digits,
                whatsapp_account=wa,
                meta_details={"permissions": perms},
            )
            return error_response(
                "Call permission required. Send a call permission request first.",
                status_code=403,
                code="whatsapp_call_permission_required",
                details={"permissions": perms},
            )

    try:
        body = graph_call_action(
            wa,
            action="connect",
            to=to_digits,
            sdp=sdp,
            sdp_type="offer",
        )
    except WhatsAppCallingError as exc:
        return _calling_error_response(
            exc,
            log_ctx={
                "company": request.user.company,
                "source": WhatsAppCallErrorSource.INITIATE.value,
                "agent": request.user,
                "client": client,
                "peer_phone": to_digits,
                "whatsapp_account": wa,
            },
        )

    meta_call_id = ""
    calls = body.get("calls") if isinstance(body, dict) else None
    if isinstance(calls, list) and calls:
        meta_call_id = str(calls[0].get("id") or "").strip()
    if not meta_call_id:
        meta_call_id = str(body.get("id") or body.get("call_id") or "").strip()
    if not meta_call_id:
        # Meta may only return success; webhook will create/update — use provisional id
        meta_call_id = f"pending.{wa.phone_number_id}.{timezone.now().timestamp()}"

    call = WhatsAppCall.objects.create(
        company=request.user.company,
        whatsapp_account=wa,
        meta_call_id=meta_call_id,
        direction=WhatsAppCallDirection.OUTBOUND,
        status=WhatsAppCallStatus.RINGING,
        peer_phone=to_digits,
        client=client,
        agent=request.user,
        offer_sdp=sdp,
        started_at=timezone.now(),
        recording_status=WhatsAppCallRecordingStatus.NONE,
        raw_payload={"initiate_response": body},
    )
    return success_response(_serialize_call(call, request), status_code=201)


DEFAULT_CALL_PERMISSION_BODY_EN = (
    "We would like to call you on WhatsApp. Please allow calls from our business."
)
DEFAULT_CALL_PERMISSION_BODY_AR = (
    "نود الاتصال بك عبر واتساب. يرجى السماح بالمكالمات من نشاطنا التجاري."
)


def default_call_permission_body(language: str | None = None) -> str:
    lang = (language or "en").strip().lower()
    if lang.startswith("ar"):
        return DEFAULT_CALL_PERMISSION_BODY_AR
    return DEFAULT_CALL_PERMISSION_BODY_EN


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_permission_request(request):
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)

    to = (request.data.get("to") or request.data.get("phone") or "").strip()
    template_id = request.data.get("template_id")
    template_name = (request.data.get("template_name") or "").strip()
    user_lang = (getattr(request.user, "language", None) or "ar").strip() or "ar"
    language = (request.data.get("language") or user_lang or "en").strip() or "en"
    body_text = (request.data.get("body") or request.data.get("body_text") or "").strip()
    account_id = request.data.get("whatsapp_account_id")

    if not to:
        return validation_error_response({"to": ["Required"]})

    wa, err = _resolve_account(request.user, account_id)
    if not wa:
        return error_response(err or "No connected WhatsApp account", status_code=400)

    company = request.user.company
    mode = "template"
    resolved_template_id = None

    if template_id:
        tmpl = MessageTemplate.objects.filter(company=company, pk=template_id).first()
        if not tmpl:
            return error_response("Template not found", status_code=404)
        from integrations.views.templates_whatsapp import meta_slug_template_name

        template_name = meta_slug_template_name(tmpl.name, tmpl.id)
        language = (tmpl.language or language or "en").strip() or "en"
        resolved_template_id = tmpl.id
    elif not template_name:
        # Auto-resolve: open session → free-form interactive; else approved CPR template.
        from datetime import timedelta

        from django.db.models import Max, Q

        from integrations.models import LeadWhatsAppMessage
        from integrations.services.phone_match import phone_match_keys

        to_digits = "".join(c for c in to if c.isdigit())
        keys = phone_match_keys(to_digits) if to_digits else []
        phone_q = Q()
        for k in keys:
            if len(k) >= 7:
                phone_q |= Q(phone_number=k) | Q(phone_number__endswith=k[-10:])
        msg_filter = Q(
            client__company=company,
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        )
        if phone_q:
            msg_filter &= phone_q
        else:
            msg_filter &= Q(pk__in=[])
        last_inbound = (
            LeadWhatsAppMessage.objects.filter(msg_filter).aggregate(m=Max("created_at"))["m"]
        )
        in_session = bool(
            last_inbound and timezone.now() < last_inbound + timedelta(hours=24)
        )

        if in_session:
            mode = "interactive"
        else:
            tmpl = find_call_permission_template(company)
            if not tmpl:
                return error_response(
                    "No approved WhatsApp call permission template found. "
                    "Create a UTILITY/MARKETING template with a call permission request "
                    "in Meta (or Template Management), sync it, then try again. "
                    "If the contact messaged you recently, open the chat and retry — "
                    "a free-form permission request can be sent inside the 24h window.",
                    status_code=400,
                    code="whatsapp_call_permission_template_missing",
                )
            from integrations.views.templates_whatsapp import meta_slug_template_name

            template_name = meta_slug_template_name(tmpl.name, tmpl.id)
            language = (tmpl.language or language or "en").strip() or "en"
            resolved_template_id = tmpl.id

    if mode != "interactive" and not template_name:
        return validation_error_response({"template_name": ["Required"]})

    try:
        if mode == "interactive":
            cpr_body = body_text or default_call_permission_body(language)
            body = send_call_permission_request_interactive(
                wa,
                to=to,
                body_text=cpr_body,
            )
            preview_body = cpr_body.strip() or "[call permission request]"
        else:
            body = send_call_permission_request(
                wa, to=to, template_name=template_name, language_code=language
            )
            preview_body = "[call permission request]"
    except WhatsAppCallingError as exc:
        return _calling_error_response(
            exc,
            log_ctx={
                "company": request.user.company,
                "source": WhatsAppCallErrorSource.PERMISSION_REQUEST.value,
                "agent": request.user,
                "peer_phone": to,
                "whatsapp_account": wa,
            },
        )

    # Persist into the chat thread so agents see the permission request (not a blank gap).
    try:
        from integrations.models import LeadWhatsAppMessage
        from integrations.services.whatsapp_client import ensure_client_for_whatsapp_phone

        to_digits = "".join(c for c in to if c.isdigit())
        client = ensure_client_for_whatsapp_phone(
            company,
            to_digits,
            integration_account=wa.integration_account,
        )
        wam_id = None
        messages = body.get("messages") if isinstance(body, dict) else None
        if isinstance(messages, list) and messages:
            wam_id = messages[0].get("id")
        if client and to_digits:
            LeadWhatsAppMessage.objects.create(
                client=client,
                phone_number=to_digits,
                body=preview_body[:65535],
                direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
                whatsapp_message_id=wam_id,
                phone_number_id=wa.phone_number_id,
                delivery_status="sent",
                created_by=request.user,
            )
    except Exception:
        logger.exception("Failed to persist call permission request message")

    return success_response(
        {
            "graph": body,
            "mode": mode,
            "template_id": resolved_template_id,
            "template_name": template_name or None,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_permissions(request):
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    to = (request.query_params.get("to") or request.query_params.get("phone") or "").strip()
    if not to:
        return validation_error_response({"to": ["Required"]})
    wa, err = _resolve_account(request.user, request.query_params.get("whatsapp_account_id"))
    if not wa:
        return error_response(err or "No connected WhatsApp account", status_code=400)
    to_digits = "".join(c for c in to if c.isdigit())
    try:
        perms = get_call_permissions(wa, to_digits)
    except WhatsAppCallingError as exc:
        return _calling_error_response(exc)
    return success_response(
        {
            "permissions": perms,
            "can_start_call": call_permission_allows_start(perms),
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_calling_enable(request):
    """Enable Cloud Calling on the connected WhatsApp phone number via Meta."""
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not request.user.is_admin():
        return error_response("Only admins can enable WhatsApp calling", status_code=403)

    wa, err = _resolve_account(request.user, request.data.get("whatsapp_account_id"))
    if not wa:
        return error_response(err or "No connected WhatsApp account", status_code=400)

    # Cloud Calling requires a Cloud-API-only number. Coexistence (Business app + API)
    # keeps voice/video on the app only — Meta rejects enable with #141000.
    integ_meta = {}
    if wa.integration_account_id and isinstance(
        getattr(wa.integration_account, "metadata", None), dict
    ):
        integ_meta = wa.integration_account.metadata or {}
    if integ_meta.get("coexistence") or integ_meta.get("is_on_biz_app") is True:
        return error_response(
            "WhatsApp Cloud Calling is not available on coexistence numbers "
            "(Business app + Cloud API). Meta keeps voice/video on the Business app only. "
            "Use a Cloud-API-only number to enable calling in the CRM.",
            code="whatsapp_calling_coexistence_unsupported",
            details={
                "coexistence": True,
                "display_phone_number": wa.display_phone_number,
                "phone_number_id": wa.phone_number_id,
            },
            status_code=400,
        )

    try:
        body = enable_calling_on_account(wa)
    except WhatsAppCallingError as exc:
        return _calling_error_response(exc)
    wa.refresh_from_db()
    return success_response(
        {
            "calling_enabled": wa.calling_enabled,
            "whatsapp_account_id": wa.id,
            "graph": body,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_recording_upload(request, pk: int):
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    call = _get_call_for_user(request.user, pk)
    if not call:
        return error_response("Call not found", status_code=404)
    if not call.answered_at:
        return error_response(
            "Recording is only stored for answered calls",
            code="whatsapp_call_not_answered",
            status_code=400,
        )
    upload = request.FILES.get("file") or request.FILES.get("recording")
    if not upload:
        return validation_error_response({"file": ["Required"]})
    notes = (request.data.get("notes") or "").strip()
    if notes:
        call.notes = notes
        call.save(update_fields=["notes", "updated_at"])
    data = upload.read()
    if not data:
        return validation_error_response({"file": ["Empty file"]})
    try:
        store_call_recording(call, file_bytes=data, original_filename=upload.name or "call.webm")
    except ValueError as exc:
        return error_response(str(exc), code="whatsapp_call_not_answered", status_code=400)
    call.refresh_from_db()
    return success_response(_serialize_call(call, request))


@api_view(["GET"])
@permission_classes([AllowAny])
def whatsapp_call_recording_play(request, pk: int):
    """Signed-token or authenticated playback (mirrors PBX recording play)."""
    token = (request.query_params.get("token") or "").strip()
    call = None
    if token:
        try:
            wid, cid = verify_wa_playback_token(token)
        except Exception:
            return error_response("Invalid or expired token", status_code=403)
        if wid != pk:
            return error_response("Invalid token", status_code=403)
        call = WhatsAppCall.objects.filter(pk=pk, company_id=cid).first()
    elif request.user and request.user.is_authenticated:
        call = WhatsAppCall.objects.filter(pk=pk, company=request.user.company).first()
        if call and not user_sees_all_company_leads(request.user):
            if call.agent_id != request.user.id and (
                not call.client_id
                or getattr(call.client, "assigned_to_id", None) != request.user.id
            ):
                call = None
    if not call:
        return error_response("Not found", status_code=404)
    try:
        fh = stream_wa_recording(call)
    except FileNotFoundError:
        raise Http404("Recording not found")
    content_type = mimetypes.guess_type(call.recording_storage_key)[0] or "audio/webm"
    return FileResponse(fh, content_type=content_type, as_attachment=False)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_agent_status(request):
    """
    Agent Ready / Away for WhatsApp calling.
    POST body: { "status": "ready" } or { "status": "away", "duration_minutes": 15|30|60 }
    """
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)

    if request.method == "GET":
        return success_response(serialize_agent_call_status(request.user))

    status = (request.data.get("status") or "").strip().lower()
    if status == "ready":
        payload = set_agent_call_away(request.user, duration_minutes=None)
        return success_response(payload)
    if status == "away":
        try:
            minutes = int(request.data.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            return validation_error_response({"duration_minutes": ["Must be 15, 30, or 60"]})
        try:
            payload = set_agent_call_away(request.user, duration_minutes=minutes)
        except ValueError as exc:
            return validation_error_response({"duration_minutes": [str(exc)]})
        return success_response(payload)
    return validation_error_response({"status": ["Must be ready or away"]})


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_agent_status_team(request):
    """
    Owner/supervisor view: Ready/Away for company users who can take WhatsApp calls.
    """
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)
    if not user_sees_all_company_leads(request.user):
        return error_response("Not allowed to view team call status", status_code=403)

    from accounts.models import User

    qs = (
        User.objects.filter(company=request.user.company, is_active=True)
        .exclude(role__in=("data_entry",))
        .order_by("first_name", "last_name", "username")
    )
    results = []
    for u in qs[:100]:
        st = serialize_agent_call_status(u)
        results.append(
            {
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name or "",
                "last_name": u.last_name or "",
                "role": u.role,
                "status": st["status"],
                "away_until": st.get("away_until"),
            }
        )
    return success_response({"results": results})


def _user_can_manage_call_hours(user) -> bool:
    role = (getattr(user, "role", "") or "").lower()
    if role in ("admin", "owner"):
        return True
    return user_sees_all_company_leads(user)


@api_view(["GET", "PUT", "PATCH"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_hours(request):
    """
    Configure weekly WhatsApp call hours + out-of-hours customer message.
    Syncs to Meta call_hours when credentials allow.
    """
    gate = _integration_gate(request.user.company, "whatsapp")
    if not gate.get("enabled"):
        return error_response(gate.get("message") or "WhatsApp disabled", status_code=403)
    if not user_can_access_whatsapp_calls(request.user):
        return error_response('WhatsApp call access is disabled for your account', code='whatsapp_access_disabled', status_code=403)

    account_id = request.query_params.get("whatsapp_account_id") or request.data.get(
        "whatsapp_account_id"
    )
    wa, err = _resolve_account(request.user, account_id)
    if not wa:
        return error_response(
            "No connected WhatsApp number for this company.",
            code=err or "no_connected_whatsapp_number",
            status_code=400,
        )

    if request.method == "GET":
        return success_response(serialize_call_hours(wa))

    if not _user_can_manage_call_hours(request.user):
        return error_response("Not allowed to manage call hours", status_code=403)

    enabled = request.data.get("enabled")
    if enabled is not None:
        wa.call_hours_enabled = bool(enabled)

    tz = request.data.get("timezone")
    if tz is not None:
        wa.call_hours_timezone = str(tz).strip()[:64]

    weekly = request.data.get("weekly")
    if weekly is not None:
        wa.call_hours_weekly = normalize_weekly_schedule(weekly)

    msg = request.data.get("out_of_hours_message")
    if msg is not None:
        wa.out_of_hours_message = str(msg)[:4000]

    sync_meta = request.data.get("sync_meta", True)
    wa.save(
        update_fields=[
            "call_hours_enabled",
            "call_hours_timezone",
            "call_hours_weekly",
            "out_of_hours_message",
            "updated_at",
        ]
    )

    meta_result = None
    meta_error = None
    if sync_meta and wa.calling_enabled:
        try:
            meta_result = sync_call_hours_to_meta(wa)
        except WhatsAppCallingError as exc:
            meta_error = str(exc)
        except Exception as exc:
            meta_error = str(exc)

    payload = serialize_call_hours(wa)
    if meta_result is not None:
        payload["meta_sync"] = meta_result
    if meta_error:
        payload["meta_sync_error"] = meta_error
    return success_response(payload)

