"""Process PBX events: CDR upsert, lead match, auto ClientCall, screen pop."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional, TypeVar

from django.db import IntegrityError, OperationalError, close_old_connections, transaction
from django.utils import timezone

from crm.models import ClientCall, ClientCallSource
from integrations.models import (
    PbxCallDisposition,
    PbxCallDirection,
    PbxCallRecord,
    PbxEventType,
    PbxSettings,
    UserPbxExtension,
)
from integrations.services.phone_match import find_client_by_phone
from integrations.services.zycoo_parser import parse_zycoo_payload
from notifications.models import NotificationType
from notifications.services import NotificationService
from settings.models import CallMethod

logger = logging.getLogger(__name__)

_MAX_PUSH_LOG_BODY = 16384
_SQLITE_LOCK_MAX_ATTEMPTS = 15
_SQLITE_LOCK_INITIAL_DELAY = 0.05
_SQLITE_LOCK_MAX_DELAY = 3.0

T = TypeVar("T")


def log_incoming_zycoo_push(
    *,
    company_id: int | None,
    source: str,
    raw_body: bytes,
    content_type: str = "",
    webhook_token_prefix: str = "",
) -> None:
    """Log every PBX push (all event types), including ones we do not act on."""
    body = raw_body or b""
    preview = body.decode("utf-8", errors="replace")
    if len(preview) > _MAX_PUSH_LOG_BODY:
        preview = (
            preview[:_MAX_PUSH_LOG_BODY]
            + f"… (truncated, {len(body)} bytes total)"
        )

    parsed_summary = ""
    try:
        parsed = parse_zycoo_payload(body, content_type)
        parsed_summary = (
            f"raw_event={parsed.get('raw_event')!r} "
            f"mapped_type={parsed.get('event_type')} "
            f"uniqueid={parsed.get('uniqueid')} "
            f"extension={parsed.get('extension')} "
            f"caller={parsed.get('caller')} "
            f"callee={parsed.get('callee')} "
            f"direction={parsed.get('direction')} "
            f"external_phone={parsed.get('external_phone')} "
            f"disposition={parsed.get('disposition')}"
        )
    except Exception as exc:
        parsed_summary = f"parse_error={exc!r}"

    logger.info(
        "ZYCOO push [%s] company_id=%s token_prefix=%s content_type=%s bytes=%s | %s | body=%s",
        source,
        company_id if company_id is not None else "unknown",
        (webhook_token_prefix or "")[:12] or "-",
        content_type or "-",
        len(body),
        parsed_summary,
        preview,
    )


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    return isinstance(exc, OperationalError) and "locked" in str(exc).lower()


def _run_with_sqlite_retry(fn: Callable[[], T], *, company_id: int, label: str) -> T:
    delay = _SQLITE_LOCK_INITIAL_DELAY
    for attempt in range(_SQLITE_LOCK_MAX_ATTEMPTS):
        try:
            return fn()
        except OperationalError as exc:
            if not _is_sqlite_lock_error(exc) or attempt >= _SQLITE_LOCK_MAX_ATTEMPTS - 1:
                raise
            close_old_connections()
            logger.warning(
                "PBX %s DB locked, retry %s/%s company_id=%s",
                label,
                attempt + 1,
                _SQLITE_LOCK_MAX_ATTEMPTS,
                company_id,
            )
            time.sleep(delay)
            delay = min(delay * 1.5, _SQLITE_LOCK_MAX_DELAY)


def _resolve_agent(company, extension: str):
    if not extension:
        return None
    try:
        mapping = UserPbxExtension.objects.select_related("user").get(
            company=company, extension=extension
        )
        return mapping.user
    except UserPbxExtension.DoesNotExist:
        return None


def _build_recording_url(settings: PbxSettings, recording_path: str, recording_url: str) -> str:
    if recording_url:
        return recording_url
    if not recording_path:
        return ""
    path = recording_path.strip()
    if path.startswith("http://") or path.startswith("https://"):
        return path

    host = (settings.pbx_host or "").strip().rstrip("/")
    if not host:
        return path

    if host.startswith("http://") or host.startswith("https://"):
        base = host.rstrip("/")
    else:
        scheme = "https" if ".zycoo.com" in host else "http"
        base = f"{scheme}://{host}"

    # ZYCOO CDR example:
    # /var/spool/asterisk/monitor/recording/20260603/104/<file>.wav
    relative = path
    for prefix in (
        "/var/spool/asterisk/monitor/recording/",
        "/var/spool/asterisk/monitor/",
        "/var/spool/asterisk/",
    ):
        if path.startswith(prefix):
            relative = path[len(prefix) :]
            break

    if relative and not relative.startswith("/"):
        return f"{base}/monitor/recording/{relative.lstrip('/')}"

    if path.startswith("/"):
        return f"{base}{path}"
    return path


def _default_call_method(company):
    return (
        CallMethod.objects.filter(company=company, is_active=True, is_default=True).first()
        or CallMethod.objects.filter(company=company, is_active=True).first()
    )


def _screen_pop_users(client, agent):
    users = []
    if agent and agent.is_active:
        users.append(agent)
    if client and client.assigned_to and client.assigned_to.is_active:
        if client.assigned_to not in users:
            users.append(client.assigned_to)
    return users


def _send_screen_pop(settings: PbxSettings, client, phone: str, record: PbxCallRecord, agent):
    if not settings.screen_pop_enabled:
        return
    users = _screen_pop_users(client, agent)
    if not users:
        return
    data = {
        "lead_id": client.id if client else None,
        "client_id": client.id if client else None,
        "client_name": client.name if client else "",
        "phone": phone,
        "call_id": record.id,
        "pbx_uniqueid": record.uniqueid,
        "open_lead": bool(client),
    }
    title = client.name if client else phone
    body = phone if client else f"Incoming call from {phone}"
    NotificationService.send_notification_to_multiple(
        users,
        NotificationType.PBX_INCOMING_CALL,
        title=title,
        body=body,
        data=data,
    )


def _send_missed_call(settings: PbxSettings, client, phone: str, record: PbxCallRecord, agent):
    users = _screen_pop_users(client, agent)
    if not users:
        return
    data = {
        "lead_id": client.id if client else None,
        "client_id": client.id if client else None,
        "phone": phone,
        "call_id": record.id,
    }
    NotificationService.send_notification_to_multiple(
        users,
        NotificationType.PBX_CALL_MISSED,
        data=data,
    )


def _pbx_timeline_score(record: PbxCallRecord) -> int:
    """Prefer inbound answered legs with talk time for the single timeline row."""
    score = record.billsec or record.duration_sec or 0
    if record.direction == PbxCallDirection.INBOUND:
        score += 1000
    if record.disposition == PbxCallDisposition.ANSWERED:
        score += 500
    return score


def _pbx_call_notes(record: PbxCallRecord) -> str:
    duration = record.billsec or record.duration_sec
    return f"{duration}s" if duration else ""


def _auto_log_client_call(settings: PbxSettings, record: PbxCallRecord, client, agent):
    if not settings.auto_log_calls or not client:
        return None
    if record.event_type not in (PbxEventType.HANGUP, PbxEventType.MISSED):
        return None

    existing_for_record = ClientCall.objects.filter(pbx_call_record=record).first()
    if existing_for_record:
        return existing_for_record

    call_key = (record.linkedid or record.uniqueid).strip()
    if call_key:
        existing = (
            ClientCall.objects.filter(
                client=client,
                source=ClientCallSource.PBX,
                pbx_call_record__company=settings.company,
                pbx_call_record__linkedid=call_key,
            )
            .select_related("pbx_call_record")
            .first()
        )
        if existing:
            prev = existing.pbx_call_record
            if prev and _pbx_timeline_score(record) > _pbx_timeline_score(prev):
                existing.pbx_call_record = record
                notes = _pbx_call_notes(record)
                if notes:
                    existing.notes = notes
                ended = record.started_at or record.ended_at
                if ended:
                    existing.call_datetime = ended
                existing.save(
                    update_fields=["pbx_call_record", "notes", "call_datetime"]
                )
            return existing

    call_method = _default_call_method(settings.company)
    return ClientCall.objects.create(
        client=client,
        call_method=call_method,
        source=ClientCallSource.PBX,
        pbx_call_record=record,
        notes=_pbx_call_notes(record),
        call_datetime=record.started_at or record.ended_at or timezone.now(),
        created_by=agent,
    )


def _record_defaults(
    settings: PbxSettings,
    parsed: dict[str, Any],
    *,
    client,
    agent,
) -> dict[str, Any]:
    return {
        "linkedid": parsed["linkedid"],
        "direction": parsed["direction"],
        "caller": parsed["caller"],
        "callee": parsed["callee"],
        "extension": parsed["extension"],
        "disposition": parsed["disposition"],
        "started_at": parsed["started_at"],
        "answered_at": parsed["answered_at"],
        "ended_at": parsed["ended_at"],
        "duration_sec": parsed["duration_sec"],
        "billsec": parsed["billsec"],
        "recording_path": parsed["recording_path"],
        "recording_url": _build_recording_url(
            settings, parsed["recording_path"], parsed["recording_url"]
        ),
        "client": client,
        "agent": agent,
        "raw_payload": parsed["raw_payload"],
    }


def _record_updates(
    settings: PbxSettings,
    record: PbxCallRecord,
    parsed: dict[str, Any],
    *,
    client,
    agent,
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if parsed["caller"]:
        updates["caller"] = parsed["caller"]
    if parsed["callee"]:
        updates["callee"] = parsed["callee"]
    if parsed["extension"]:
        updates["extension"] = parsed["extension"]
    if parsed["disposition"] != PbxCallDisposition.UNKNOWN:
        updates["disposition"] = parsed["disposition"]
    if parsed["duration_sec"]:
        updates["duration_sec"] = parsed["duration_sec"]
    if parsed["billsec"]:
        updates["billsec"] = parsed["billsec"]
    rec_url = _build_recording_url(
        settings, parsed["recording_path"], parsed["recording_url"]
    )
    if rec_url:
        updates["recording_url"] = rec_url
    if parsed["recording_path"]:
        updates["recording_path"] = parsed["recording_path"]
    if parsed["answered_at"]:
        updates["answered_at"] = parsed["answered_at"]
    if parsed["ended_at"]:
        updates["ended_at"] = parsed["ended_at"]
    if client and not record.client_id:
        updates["client"] = client
    if agent and not record.agent_id:
        updates["agent"] = agent
    if parsed["linkedid"] and not record.linkedid:
        updates["linkedid"] = parsed["linkedid"]
    return updates


def _persist_pbx_call_record(
    settings: PbxSettings,
    parsed: dict[str, Any],
    *,
    client,
    agent,
) -> tuple[PbxCallRecord, bool]:
    """Upsert call record in a short transaction (no notifications)."""
    company = settings.company
    uniqueid = parsed["uniqueid"]
    event_type = parsed["event_type"]
    lookup = {
        "company": company,
        "uniqueid": uniqueid,
        "event_type": event_type,
    }
    defaults = _record_defaults(settings, parsed, client=client, agent=agent)

    with transaction.atomic():
        try:
            record, created = PbxCallRecord.objects.get_or_create(
                **lookup,
                defaults=defaults,
            )
        except IntegrityError:
            record = PbxCallRecord.objects.get(**lookup)
            created = False

        if not created:
            updates = _record_updates(
                settings, record, parsed, client=client, agent=agent
            )
            if updates:
                for key, value in updates.items():
                    setattr(record, key, value)
                record.save(update_fields=list(updates.keys()) + ["updated_at"])

    return record, created


def _apply_pbx_side_effects(
    settings: PbxSettings,
    parsed: dict[str, Any],
    record: PbxCallRecord,
    *,
    client,
    agent,
    external_phone: str,
) -> Optional[ClientCall]:
    event_type = parsed["event_type"]

    if event_type == PbxEventType.RINGING and parsed["direction"] == PbxCallDirection.INBOUND:
        _send_screen_pop(settings, client, external_phone, record, agent)
    elif event_type == PbxEventType.MISSED or (
        event_type == PbxEventType.HANGUP
        and record.disposition in (PbxCallDisposition.NO_ANSWER, PbxCallDisposition.BUSY)
    ):
        _send_missed_call(settings, client, external_phone, record, agent)

    if event_type in (PbxEventType.HANGUP, PbxEventType.MISSED):
        return _run_with_sqlite_retry(
            lambda: _auto_log_client_call(settings, record, client, agent),
            company_id=settings.company_id,
            label="client_call",
        )
    return None


def process_pbx_payload(
    settings: PbxSettings,
    raw_body: bytes,
    content_type: str = "",
    *,
    source: str = "webhook",
    webhook_token_prefix: str = "",
) -> dict[str, Any]:
    """Parse and apply a PBX webhook/connector event."""
    log_incoming_zycoo_push(
        company_id=settings.company_id,
        source=source,
        raw_body=raw_body,
        content_type=content_type,
        webhook_token_prefix=webhook_token_prefix,
    )

    if not settings.is_enabled:
        logger.info(
            "ZYCOO push ignored (integration disabled) company_id=%s source=%s",
            settings.company_id,
            source,
        )
        return {"ok": False, "reason": "pbx_disabled"}

    parsed = parse_zycoo_payload(raw_body, content_type)
    company = settings.company
    agent = _resolve_agent(company, parsed["extension"])
    external_phone = parsed["external_phone"] or ""
    client = find_client_by_phone(company, external_phone) if external_phone else None

    record, created = _run_with_sqlite_retry(
        lambda: _persist_pbx_call_record(
            settings, parsed, client=client, agent=agent
        ),
        company_id=settings.company_id,
        label="persist",
    )

    client_call = _apply_pbx_side_effects(
        settings,
        parsed,
        record,
        client=client,
        agent=agent,
        external_phone=external_phone,
    )

    event_type = parsed["event_type"]
    uniqueid = parsed["uniqueid"]
    logger.info(
        "ZYCOO push processed company_id=%s source=%s event_type=%s uniqueid=%s "
        "record_id=%s created=%s client_id=%s",
        settings.company_id,
        source,
        event_type,
        uniqueid,
        record.id,
        created,
        client.id if client else None,
    )

    return {
        "ok": True,
        "created": created,
        "record_id": record.id,
        "client_id": client.id if client else None,
        "client_call_id": client_call.id if client_call else None,
        "event_type": event_type,
    }
