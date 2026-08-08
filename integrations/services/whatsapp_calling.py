"""
WhatsApp Cloud API Calling — Graph signaling helpers + CRM call lifecycle.

Media is browser WebRTC to Meta;
recordings are uploaded from the agent browser after hangup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Any, Optional

import requests
from django.core import signing
from django.db import transaction
from django.utils import timezone

from integrations.models import (
    WhatsAppAccount,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallRecordingStatus,
    WhatsAppCallStatus,
)
from integrations.oauth_utils import META_GRAPH_API_BASE_URL
from integrations.storage.recordings import open_recording, save_recording

logger = logging.getLogger(__name__)

_PLAY_TOKEN_SALT = "whatsapp-call-recording-play"
_PLAY_TOKEN_MAX_AGE = 60 * 60 * 24  # 24 hours


class WhatsAppCallingError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _token(account: WhatsAppAccount) -> str:
    token = account.get_access_token()
    if not token:
        raise WhatsAppCallingError("WhatsApp account has no access token")
    return token


def _graph_post(account: WhatsAppAccount, path: str, payload: dict) -> dict:
    url = f"{META_GRAPH_API_BASE_URL}/{path.lstrip('/')}"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {_token(account)}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise WhatsAppCallingError(f"Graph request failed: {exc}") from exc

    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        body = {"raw": (resp.text or "")[:500]}

    if resp.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        msg = ""
        if isinstance(err, dict):
            msg = err.get("message") or err.get("error_user_msg") or ""
        raise WhatsAppCallingError(
            msg or f"Graph error HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )
    return body if isinstance(body, dict) else {"data": body}


def _graph_get(account: WhatsAppAccount, path: str, params: dict | None = None) -> dict:
    url = f"{META_GRAPH_API_BASE_URL}/{path.lstrip('/')}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {_token(account)}"},
            params=params or {},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise WhatsAppCallingError(f"Graph request failed: {exc}") from exc

    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        body = {"raw": (resp.text or "")[:500]}

    if resp.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        msg = ""
        if isinstance(err, dict):
            msg = err.get("message") or ""
        raise WhatsAppCallingError(
            msg or f"Graph error HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )
    return body if isinstance(body, dict) else {"data": body}


def is_seed_whatsapp_account(account: WhatsAppAccount) -> bool:
    """Local UI-review seed rows must never hit Meta Graph."""
    pid = (account.phone_number_id or "").strip()
    if pid.startswith("seed_"):
        return True
    token = (account.get_access_token() or "").strip()
    return token.startswith("seed-fake")


def enable_calling_on_account(account: WhatsAppAccount) -> dict:
    """POST /{phone-number-id}/settings — enable Cloud Calling."""
    if is_seed_whatsapp_account(account):
        if not account.calling_enabled:
            account.calling_enabled = True
            account.save(update_fields=["calling_enabled", "updated_at"])
        return {"success": True, "seed": True, "calling": {"status": "ENABLED"}}

    body = _graph_post(
        account,
        f"{account.phone_number_id}/settings",
        {"calling": {"status": "ENABLED"}},
    )
    if not account.calling_enabled:
        account.calling_enabled = True
        account.save(update_fields=["calling_enabled", "updated_at"])
    return body


def get_calling_settings(account: WhatsAppAccount) -> dict:
    return _graph_get(account, f"{account.phone_number_id}/settings")


def get_call_permissions(account: WhatsAppAccount, user_wa_id: str) -> dict:
    return _graph_get(
        account,
        f"{account.phone_number_id}/call_permissions",
        params={"user_wa_id": user_wa_id},
    )


def call_permission_allows_start(permission_payload: dict) -> bool:
    """Best-effort parse of GET call_permissions response."""
    if not isinstance(permission_payload, dict):
        return False
    permission = permission_payload.get("permission") or permission_payload
    if isinstance(permission, dict):
        status = (
            permission.get("status") or permission.get("permission_status") or ""
        ).lower()
        if status in ("temporary", "permanent", "granted", "allowed", "yes"):
            return True
        if permission.get("is_permanent") is True:
            return True
        exp = permission.get("expiration_time") or permission.get("expiration_timestamp")
        if exp:
            try:
                ts = int(exp)
                if ts > timezone.now().timestamp():
                    return True
            except (TypeError, ValueError):
                pass
    actions = permission_payload.get("actions") or []
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            name = (action.get("action_name") or action.get("name") or "").lower()
            if name in ("start_call", "call"):
                can = action.get("can_perform_action")
                if can is True or str(can).lower() == "true":
                    return True
                limits = action.get("limits") or []
                if can is None and limits:
                    return True
    return False


def graph_call_action(
    account: WhatsAppAccount,
    *,
    action: str,
    call_id: str | None = None,
    to: str | None = None,
    sdp: str | None = None,
    sdp_type: str | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "action": action,
    }
    if call_id:
        payload["call_id"] = call_id
    if to:
        payload["to"] = "".join(c for c in to if c.isdigit())
    if sdp and sdp_type:
        payload["session"] = {"sdp_type": sdp_type, "sdp": sdp}
    return _graph_post(account, f"{account.phone_number_id}/calls", payload)


def send_call_permission_request(
    account: WhatsAppAccount,
    *,
    to: str,
    template_name: str,
    language_code: str = "en",
    components: list | None = None,
) -> dict:
    """Send a Meta call_permission_request template message."""
    to_digits = "".join(c for c in to if c.isdigit())
    template_block: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_block["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "to": to_digits,
        "type": "template",
        "template": template_block,
    }
    return _graph_post(account, f"{account.phone_number_id}/messages", payload)


def send_call_permission_request_interactive(
    account: WhatsAppAccount,
    *,
    to: str,
    body_text: str | None = None,
) -> dict:
    """
    Free-form interactive call permission request (requires open customer service window).
    """
    to_digits = "".join(c for c in to if c.isdigit())
    interactive: dict[str, Any] = {
        "type": "call_permission_request",
        "action": {"name": "call_permission_request"},
    }
    text = (body_text or "").strip()
    if text:
        interactive["body"] = {"text": text[:1024]}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_digits,
        "type": "interactive",
        "interactive": interactive,
    }
    return _graph_post(account, f"{account.phone_number_id}/messages", payload)


def template_has_call_permission_request(template) -> bool:
    """True if CRM template includes Meta call_permission_request (buttons or name heuristic)."""
    buttons = getattr(template, "buttons", None) or []
    if isinstance(buttons, list):
        for btn in buttons:
            if not isinstance(btn, dict):
                continue
            btype = (btn.get("type") or "").lower().replace(" ", "_")
            if btype in ("call_permission_request", "call_permission"):
                return True
    name = (getattr(template, "name", None) or "").lower()
    return "call_permission" in name


def find_call_permission_template(company):
    """
    Pick an APPROVED WhatsApp template suitable for call permission requests.
    Prefers templates with an explicit call_permission_request marker and no body vars.
    """
    from integrations.models import MessageTemplate
    from integrations.views.templates_whatsapp import count_template_body_placeholders

    qs = MessageTemplate.objects.filter(
        company=company,
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        meta_status__iexact="APPROVED",
    ).order_by("-updated_at")

    marked: list = []
    heuristic: list = []
    for tmpl in qs:
        if template_has_call_permission_request(tmpl):
            if count_template_body_placeholders(tmpl.content or "") == 0:
                marked.append(tmpl)
            else:
                heuristic.append(tmpl)
        elif "call_permission" in (tmpl.name or "").lower():
            if count_template_body_placeholders(tmpl.content or "") == 0:
                heuristic.append(tmpl)

    if marked:
        return marked[0]
    if heuristic:
        # Prefer zero-placeholder heuristic hits already collected; else any marked-with-vars
        return heuristic[0]
    return None


def _parse_ts(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        ts = int(value)
        return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    except (TypeError, ValueError):
        return None


def _contact_name(value: dict) -> str:
    contacts = value.get("contacts") or []
    if contacts and isinstance(contacts[0], dict):
        profile = contacts[0].get("profile") or {}
        return (profile.get("name") or "").strip()
    return ""


@transaction.atomic
def process_calls_webhook_value(value: dict, *, waba_id: str | None = None) -> list[WhatsAppCall]:
    """Handle field=calls webhook value; returns touched WhatsAppCall rows."""
    metadata = value.get("metadata") or {}
    phone_number_id = str(metadata.get("phone_number_id") or "").strip()
    if not phone_number_id:
        logger.warning("WhatsApp calls webhook missing phone_number_id")
        return []

    account = (
        WhatsAppAccount.objects.select_related("company")
        .filter(phone_number_id=phone_number_id)
        .first()
    )
    if not account:
        logger.warning(
            "WhatsApp calls webhook: unknown phone_number_id=%s waba_id=%s",
            phone_number_id,
            waba_id,
        )
        return []

    peer_name = _contact_name(value)
    results: list[WhatsAppCall] = []

    for call_obj in value.get("calls") or []:
        if not isinstance(call_obj, dict):
            continue
        call = _upsert_from_call_event(account, call_obj, peer_name=peer_name, raw_value=value)
        if call:
            results.append(call)

    for status_obj in value.get("statuses") or []:
        if not isinstance(status_obj, dict):
            continue
        call = _apply_status_event(account, status_obj, raw_value=value)
        if call:
            results.append(call)

    return results


def _upsert_from_call_event(
    account: WhatsAppAccount,
    call_obj: dict,
    *,
    peer_name: str,
    raw_value: dict,
) -> Optional[WhatsAppCall]:
    from integrations.services.whatsapp_client import ensure_client_for_whatsapp_phone

    meta_call_id = str(call_obj.get("id") or "").strip()
    if not meta_call_id:
        return None

    event = (call_obj.get("event") or "").strip().lower()
    direction_raw = (call_obj.get("direction") or "").upper()
    direction = (
        WhatsAppCallDirection.OUTBOUND
        if direction_raw in ("BUSINESS_INITIATED", "BUSINESS", "OUTBOUND")
        else WhatsAppCallDirection.INBOUND
    )

    peer = ""
    if direction == WhatsAppCallDirection.INBOUND:
        peer = str(call_obj.get("from") or "").strip()
    else:
        peer = str(call_obj.get("to") or "").strip()

    session = call_obj.get("session") or {}
    sdp = (session.get("sdp") or "") if isinstance(session, dict) else ""
    sdp_type = ((session.get("sdp_type") or "") if isinstance(session, dict) else "").lower()

    call, _created = WhatsAppCall.objects.select_for_update().get_or_create(
        whatsapp_account=account,
        meta_call_id=meta_call_id,
        defaults={
            "company": account.company,
            "direction": direction,
            "status": WhatsAppCallStatus.RINGING,
            "peer_phone": peer,
            "peer_name": peer_name,
            "started_at": _parse_ts(call_obj.get("timestamp")) or timezone.now(),
            "raw_payload": raw_value,
        },
    )

    updates: list[str] = []
    if peer and call.peer_phone != peer:
        call.peer_phone = peer
        updates.append("peer_phone")
    if peer_name and call.peer_name != peer_name:
        call.peer_name = peer_name
        updates.append("peer_name")
    if direction and call.direction != direction:
        call.direction = direction
        updates.append("direction")

    if sdp:
        if sdp_type == "offer" or (
            not sdp_type and direction == WhatsAppCallDirection.INBOUND
        ):
            call.offer_sdp = sdp
            updates.append("offer_sdp")
        elif sdp_type == "answer":
            # Only real SDP answers mean the peer picked up — never treat outbound
            # offer echoes as answer (that would start recording before answer).
            call.answer_sdp = sdp
            updates.append("answer_sdp")
            if not call.answered_at:
                call.answered_at = timezone.now()
                updates.append("answered_at")
            if call.status not in (
                WhatsAppCallStatus.ENDED,
                WhatsAppCallStatus.MISSED,
                WhatsAppCallStatus.REJECTED,
                WhatsAppCallStatus.FAILED,
                WhatsAppCallStatus.NO_ANSWER,
            ):
                if call.status != WhatsAppCallStatus.ANSWERED:
                    call.status = WhatsAppCallStatus.ANSWERED
                    updates.append("status")
            if call.recording_status == WhatsAppCallRecordingStatus.NONE:
                call.recording_status = WhatsAppCallRecordingStatus.PENDING
                updates.append("recording_status")

    if event == "connect":
        if call.status not in (
            WhatsAppCallStatus.ENDED,
            WhatsAppCallStatus.MISSED,
            WhatsAppCallStatus.REJECTED,
            WhatsAppCallStatus.FAILED,
            WhatsAppCallStatus.NO_ANSWER,
            WhatsAppCallStatus.ANSWERED,
        ):
            if call.status != WhatsAppCallStatus.RINGING:
                call.status = WhatsAppCallStatus.RINGING
                updates.append("status")
        if not call.started_at:
            call.started_at = _parse_ts(call_obj.get("timestamp")) or timezone.now()
            updates.append("started_at")

    elif event == "terminate":
        _apply_terminate(call, call_obj, updates)

    call.raw_payload = raw_value
    updates.append("raw_payload")
    updates.append("updated_at")

    if not call.client_id and call.peer_phone:
        client = ensure_client_for_whatsapp_phone(
            account.company,
            call.peer_phone,
            integration_account=account.integration_account,
        )
        if client:
            call.client = client
            updates.append("client")

    call.save(update_fields=list(dict.fromkeys(updates)))
    if event == "terminate":
        ensure_client_call_for_whatsapp_call(call)
    elif (
        event == "connect"
        and direction == WhatsAppCallDirection.INBOUND
        and call.status == WhatsAppCallStatus.RINGING
    ):
        try:
            from integrations.services.whatsapp_call_availability import (
                is_within_call_hours,
                reject_inbound_out_of_hours,
            )

            if not is_within_call_hours(account):
                reject_inbound_out_of_hours(call)
                call.refresh_from_db()
        except Exception:
            logger.exception("Out-of-hours inbound handling failed call=%s", call.id)
    return call


def _apply_status_event(
    account: WhatsAppAccount,
    status_obj: dict,
    *,
    raw_value: dict,
) -> Optional[WhatsAppCall]:
    meta_call_id = str(status_obj.get("id") or status_obj.get("call_id") or "").strip()
    if not meta_call_id:
        return None
    call = (
        WhatsAppCall.objects.select_for_update()
        .filter(whatsapp_account=account, meta_call_id=meta_call_id)
        .first()
    )
    if not call:
        return None

    status = (status_obj.get("status") or status_obj.get("type") or "").upper()
    updates: list[str] = ["raw_payload", "updated_at"]
    call.raw_payload = raw_value

    if status in ("RINGING",):
        if call.status != WhatsAppCallStatus.ANSWERED:
            call.status = WhatsAppCallStatus.RINGING
            updates.append("status")
    elif status in ("ACCEPTED", "ANSWERED"):
        call.status = WhatsAppCallStatus.ANSWERED
        updates.append("status")
        if not call.answered_at:
            call.answered_at = timezone.now()
            updates.append("answered_at")
        if call.recording_status == WhatsAppCallRecordingStatus.NONE:
            call.recording_status = WhatsAppCallRecordingStatus.PENDING
            updates.append("recording_status")
    elif status in ("REJECTED",):
        call.status = WhatsAppCallStatus.REJECTED
        updates.append("status")
        if not call.ended_at:
            call.ended_at = timezone.now()
            updates.append("ended_at")
        if call.recording_status == WhatsAppCallRecordingStatus.PENDING and not call.answered_at:
            call.recording_status = WhatsAppCallRecordingStatus.NONE
            updates.append("recording_status")
    elif status in ("FAILED",):
        call.status = WhatsAppCallStatus.FAILED
        updates.append("status")
        if not call.ended_at:
            call.ended_at = timezone.now()
            updates.append("ended_at")
        if call.recording_status == WhatsAppCallRecordingStatus.PENDING and not call.answered_at:
            call.recording_status = WhatsAppCallRecordingStatus.NONE
            updates.append("recording_status")

    call.save(update_fields=list(dict.fromkeys(updates)))
    return call


def _apply_terminate(call: WhatsAppCall, call_obj: dict, updates: list[str]) -> None:
    start = _parse_ts(call_obj.get("start_time"))
    end = _parse_ts(call_obj.get("end_time")) or timezone.now()
    duration = call_obj.get("duration")
    try:
        duration_sec = int(duration) if duration is not None else 0
    except (TypeError, ValueError):
        duration_sec = 0

    if start and not call.started_at:
        call.started_at = start
        updates.append("started_at")
    if not call.ended_at:
        call.ended_at = end
        updates.append("ended_at")
    if duration_sec:
        call.duration_sec = duration_sec
        updates.append("duration_sec")
    elif call.answered_at and call.ended_at:
        call.duration_sec = max(0, int((call.ended_at - call.answered_at).total_seconds()))
        updates.append("duration_sec")

    errors = call_obj.get("errors") or []
    if errors and isinstance(errors, list):
        call.error_message = str(errors[0])[:2000]
        updates.append("error_message")

    if call.status in (WhatsAppCallStatus.RINGING,):
        if call.direction == WhatsAppCallDirection.INBOUND and not call.answered_at:
            call.status = WhatsAppCallStatus.MISSED
        elif call.direction == WhatsAppCallDirection.OUTBOUND and not call.answered_at:
            call.status = WhatsAppCallStatus.NO_ANSWER
        else:
            call.status = WhatsAppCallStatus.ENDED
        updates.append("status")
    elif call.status == WhatsAppCallStatus.ANSWERED:
        call.status = WhatsAppCallStatus.ENDED
        updates.append("status")

    # Unanswered attempts must not keep a pending recording expectation.
    if not call.answered_at and call.recording_status == WhatsAppCallRecordingStatus.PENDING:
        call.recording_status = WhatsAppCallRecordingStatus.NONE
        updates.append("recording_status")


def ensure_client_call_for_whatsapp_call(call: WhatsAppCall):
    """Create or refresh CRM ClientCall for timeline when call has a matched lead."""
    from crm.models import ClientCall, ClientCallSource

    if not call.client_id:
        return None
    if call.client_call_id:
        cc = call.client_call
        notes = call.notes or cc.notes or ""
        changed = False
        if notes and cc.notes != notes:
            cc.notes = notes
            changed = True
        if call.ended_at or call.answered_at or call.started_at:
            dt = call.answered_at or call.started_at or call.ended_at
            if cc.call_datetime != dt:
                cc.call_datetime = dt
                changed = True
        if changed:
            cc.save(update_fields=["notes", "call_datetime", "updated_at"])
        return cc

    if call.status not in (
        WhatsAppCallStatus.ENDED,
        WhatsAppCallStatus.ANSWERED,
        WhatsAppCallStatus.MISSED,
        WhatsAppCallStatus.REJECTED,
        WhatsAppCallStatus.NO_ANSWER,
        WhatsAppCallStatus.FAILED,
    ):
        return None

    direction_label = (
        "Incoming" if call.direction == WhatsAppCallDirection.INBOUND else "Outgoing"
    )
    status_label = call.get_status_display()
    duration = call.duration_sec or 0
    auto_notes = f"WhatsApp call · {direction_label} · {status_label}"
    if duration:
        auto_notes += f" · {duration}s"
    if call.notes:
        auto_notes = f"{auto_notes}\n{call.notes}"

    cc = ClientCall.objects.create(
        client_id=call.client_id,
        source=ClientCallSource.WHATSAPP,
        notes=auto_notes,
        call_datetime=call.answered_at
        or call.started_at
        or call.ended_at
        or timezone.now(),
        created_by=call.agent,
        dialed_phone_number=(call.peer_phone or "")[:32],
        recording_storage_key=call.recording_storage_key or "",
        recording_status=call.recording_status or "",
        recording_duration_sec=call.duration_sec or None,
    )
    call.client_call = cc
    call.save(update_fields=["client_call", "updated_at"])
    return cc


def mark_call_answered(call: WhatsAppCall, *, agent, answer_sdp: str = "") -> WhatsAppCall:
    call.agent = agent
    call.status = WhatsAppCallStatus.ANSWERED
    call.answered_at = call.answered_at or timezone.now()
    if answer_sdp:
        call.answer_sdp = answer_sdp
    call.recording_status = WhatsAppCallRecordingStatus.PENDING
    call.save(
        update_fields=[
            "agent",
            "status",
            "answered_at",
            "answer_sdp",
            "recording_status",
            "updated_at",
        ]
    )
    return call


def mark_call_rejected(call: WhatsAppCall, *, agent) -> WhatsAppCall:
    call.agent = agent
    call.status = WhatsAppCallStatus.REJECTED
    call.ended_at = timezone.now()
    updates = ["agent", "status", "ended_at", "updated_at"]
    if not call.answered_at and call.recording_status == WhatsAppCallRecordingStatus.PENDING:
        call.recording_status = WhatsAppCallRecordingStatus.NONE
        updates.append("recording_status")
    call.save(update_fields=updates)
    ensure_client_call_for_whatsapp_call(call)
    return call


def store_call_recording(
    call: WhatsAppCall,
    *,
    file_bytes: bytes,
    original_filename: str,
) -> WhatsAppCall:
    if not call.answered_at:
        raise ValueError("Cannot store recording for a call that was never answered")
    key = save_recording(
        company_id=call.company_id,
        linkedid=call.meta_call_id or str(call.id),
        file_bytes=file_bytes,
        original_filename=original_filename or "call.webm",
        prefix="whatsapp_calls",
    )
    call.recording_storage_key = key
    call.recording_status = WhatsAppCallRecordingStatus.READY
    call.offer_sdp = ""
    call.answer_sdp = ""
    call.save(
        update_fields=[
            "recording_storage_key",
            "recording_status",
            "offer_sdp",
            "answer_sdp",
            "updated_at",
        ]
    )
    ensure_client_call_for_whatsapp_call(call)
    return call


def sign_wa_playback_token(call_id: int, company_id: int) -> str:
    return signing.dumps(
        {"wid": call_id, "cid": company_id},
        salt=_PLAY_TOKEN_SALT,
    )


def verify_wa_playback_token(token: str) -> tuple[int, int]:
    data = signing.loads(token, salt=_PLAY_TOKEN_SALT, max_age=_PLAY_TOKEN_MAX_AGE)
    return int(data["wid"]), int(data["cid"])


def get_wa_playback_url(call: WhatsAppCall, request=None) -> str | None:
    if call.recording_status != WhatsAppCallRecordingStatus.READY:
        return None
    if not call.recording_storage_key:
        return None
    token = sign_wa_playback_token(call.id, call.company_id)
    path = f"/api/integrations/whatsapp/calls/{call.id}/recording/play/?token={token}"
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def stream_wa_recording(call: WhatsAppCall):
    if call.recording_status != WhatsAppCallRecordingStatus.READY:
        raise FileNotFoundError("recording not ready")
    if not call.recording_storage_key:
        raise FileNotFoundError("missing storage key")
    return open_recording(call.recording_storage_key)
