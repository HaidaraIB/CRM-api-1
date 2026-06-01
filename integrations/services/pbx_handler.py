"""Process PBX events: CDR upsert, lead match, auto ClientCall, screen pop."""

from __future__ import annotations

import logging
from typing import Any, Optional

from django.db import transaction
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
    host = (settings.pbx_host or "").strip().rstrip("/")
    if host and recording_path.startswith("/"):
        return f"http://{host}{recording_path}"
    return recording_path


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


def _auto_log_client_call(settings: PbxSettings, record: PbxCallRecord, client, agent):
    if not settings.auto_log_calls or not client:
        return None
    if record.event_type not in (PbxEventType.HANGUP, PbxEventType.MISSED):
        return None
    if ClientCall.objects.filter(pbx_call_record=record).exists():
        return ClientCall.objects.filter(pbx_call_record=record).first()

    duration = record.billsec or record.duration_sec
    notes_parts = []
    if duration:
        notes_parts.append(f"{duration}s")

    call_method = _default_call_method(settings.company)
    return ClientCall.objects.create(
        client=client,
        call_method=call_method,
        source=ClientCallSource.PBX,
        pbx_call_record=record,
        notes=" · ".join(notes_parts) if notes_parts else "",
        call_datetime=record.started_at or record.ended_at or timezone.now(),
        created_by=agent,
    )


@transaction.atomic
def process_pbx_payload(settings: PbxSettings, raw_body: bytes, content_type: str = "") -> dict[str, Any]:
    """Parse and apply a PBX webhook/connector event."""
    if not settings.is_enabled:
        return {"ok": False, "reason": "pbx_disabled"}

    parsed = parse_zycoo_payload(raw_body, content_type)
    company = settings.company
    uniqueid = parsed["uniqueid"]
    event_type = parsed["event_type"]

    agent = _resolve_agent(company, parsed["extension"])
    external_phone = parsed["external_phone"]
    client = find_client_by_phone(company, external_phone) if external_phone else None

    record, created = PbxCallRecord.objects.get_or_create(
        company=company,
        uniqueid=uniqueid,
        event_type=event_type,
        defaults={
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
        },
    )

    if not created:
        updates = {}
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
        if updates:
            for k, v in updates.items():
                setattr(record, k, v)
            record.save(update_fields=list(updates.keys()) + ["updated_at"])

    client_call = None
    if event_type == PbxEventType.RINGING and parsed["direction"] == PbxCallDirection.INBOUND:
        _send_screen_pop(settings, client, external_phone, record, agent)
    elif event_type == PbxEventType.MISSED or (
        event_type == PbxEventType.HANGUP
        and record.disposition in (PbxCallDisposition.NO_ANSWER, PbxCallDisposition.BUSY)
    ):
        _send_missed_call(settings, client, external_phone, record, agent)

    if event_type in (PbxEventType.HANGUP, PbxEventType.MISSED):
        client_call = _auto_log_client_call(settings, record, client, agent)

    return {
        "ok": True,
        "created": created,
        "record_id": record.id,
        "client_id": client.id if client else None,
        "client_call_id": client_call.id if client_call else None,
        "event_type": event_type,
    }
