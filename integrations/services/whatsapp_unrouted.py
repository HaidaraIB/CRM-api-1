"""
Hold inbound WhatsApp messages that could not be routed, and replay them later.

Meta treats HTTP 200 as delivered and does not retry. A number that is
disconnected (or a CRM sender who hid their phone) would otherwise lose the
message. Rows are replayed through ``process_whatsapp_message`` once an owner
exists; duplicate wamid checks on the thread tables make a second replay a no-op.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.utils import timezone

from ..models import (
    WhatsAppAccount,
    WhatsAppUnroutedMessage,
    WhatsAppUnroutedReason,
)

logger = logging.getLogger(__name__)

_PAYLOAD_MARKER = "WhatsApp webhook payload: "


def hold_unrouted_message(
    *,
    message: dict,
    phone_number_id,
    reason: str,
    waba_id: str | None = None,
    contacts=None,
    company=None,
    replay_error: str = "",
) -> WhatsAppUnroutedMessage | None:
    """Store one inbound message. A pending row with the same wamid is reused."""
    pid = str(phone_number_id or "").strip()
    if not pid or not isinstance(message, dict):
        return None
    external_id = str(message.get("id") or "").strip()[:128]
    if external_id:
        existing = WhatsAppUnroutedMessage.objects.filter(
            phone_number_id=pid,
            external_message_id=external_id,
            replayed_at__isnull=True,
        ).first()
        if existing is not None:
            return existing
    if company is None:
        company = (
            WhatsAppAccount.objects.filter(phone_number_id=pid)
            .values_list("company_id", flat=True)
            .first()
        )
        company_id = company
        company = None
    else:
        company_id = getattr(company, "id", company)
        company = None
    from companies.models import Company

    company_obj = Company.objects.filter(pk=company_id).first() if company_id else None
    return WhatsAppUnroutedMessage.objects.create(
        phone_number_id=pid,
        waba_id=str(waba_id or "").strip()[:64],
        external_message_id=external_id,
        company=company_obj,
        reason=reason,
        message=message,
        contacts=list(contacts or []),
        replay_error=(replay_error or "")[:2000],
    )


def _message_was_delivered(external_message_id: str) -> bool:
    if not external_message_id:
        return False
    from companies.models import AdminTenantWhatsAppMessage
    from ..models import LeadWhatsAppMessage, SocialMessage

    if LeadWhatsAppMessage.objects.filter(whatsapp_message_id=external_message_id).exists():
        return True
    if SocialMessage.objects.filter(external_message_id=external_message_id).exists():
        return True
    return AdminTenantWhatsAppMessage.objects.filter(
        whatsapp_message_id=external_message_id
    ).exists()


def replay_unrouted(phone_number_id: str | None = None) -> dict:
    """
    Feed pending rows back through inbound processing.

    Returns counts: replayed, still_held, skipped, failed.
    """
    from integrations.whatsapp_webhook import _contact_names, process_whatsapp_message

    qs = WhatsAppUnroutedMessage.objects.filter(replayed_at__isnull=True).order_by("received_at", "id")
    pid = str(phone_number_id or "").strip()
    if pid:
        qs = qs.filter(phone_number_id=pid)
    ids = list(qs.values_list("id", flat=True))
    result = {"replayed": 0, "still_held": 0, "failed": 0}
    for pk in ids:
        row = WhatsAppUnroutedMessage.objects.filter(pk=pk, replayed_at__isnull=True).first()
        if row is None:
            continue
        names = _contact_names({"contacts": row.contacts or []})
        sender_key = str((row.message or {}).get("from") or (row.message or {}).get("from_user_id") or "")
        try:
            process_whatsapp_message(
                row.message or {},
                row.phone_number_id,
                profile_name=names.get(sender_key),
                contacts=row.contacts,
                waba_id=row.waba_id or None,
            )
        except Exception as exc:
            row.replay_error = str(exc)[:2000]
            row.save(update_fields=["replay_error"])
            result["failed"] += 1
            logger.exception(
                "WhatsApp unrouted replay failed id=%s phone_number_id=%s",
                row.id,
                row.phone_number_id,
            )
            continue
        if _message_was_delivered(row.external_message_id):
            row.replayed_at = timezone.now()
            row.replay_error = ""
            row.save(update_fields=["replayed_at", "replay_error"])
            result["replayed"] += 1
        else:
            if not row.replay_error:
                row.replay_error = "still_unrouted"
                row.save(update_fields=["replay_error"])
            result["still_held"] += 1
    return result


def purge_unrouted(older_than_days: int) -> int:
    cutoff = timezone.now() - timedelta(days=int(older_than_days))
    deleted, _ = WhatsAppUnroutedMessage.objects.filter(received_at__lt=cutoff).delete()
    return deleted


def iter_webhook_payloads_from_log(text: str):
    """Yield JSON objects logged at DEBUG as ``WhatsApp webhook payload:``."""
    idx = 0
    while True:
        found = text.find(_PAYLOAD_MARKER, idx)
        if found < 0:
            return
        start = text.find("{", found)
        if start < 0:
            return
        depth = 0
        end = None
        for pos in range(start, len(text)):
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = pos + 1
                    break
        if end is None:
            return
        raw = text[start:end]
        idx = end
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def import_webhook_payload(payload: dict, *, apply: bool) -> int:
    """
    Count inbound messages in one webhook body.

    When ``apply`` is true, run them through ``process_whatsapp_message`` so a
    connected number stores them and a disconnected one holds them for replay.
    """
    from integrations.whatsapp_webhook import _contact_names, process_whatsapp_message

    count = 0
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        waba_id = str(entry.get("id") or "").strip() or None
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            messages = value.get("messages") or []
            if not messages:
                continue
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            if not phone_number_id:
                continue
            names = _contact_names(value)
            contacts = value.get("contacts") or []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                count += 1
                if not apply:
                    continue
                sender_key = str(message.get("from") or message.get("from_user_id") or "")
                try:
                    process_whatsapp_message(
                        message,
                        phone_number_id,
                        profile_name=names.get(sender_key),
                        contacts=contacts,
                        waba_id=waba_id,
                    )
                except Exception:
                    logger.exception(
                        "WhatsApp log import failed phone_number_id=%s",
                        phone_number_id,
                    )
    return count


def replay_after_connect(phone_number_id) -> None:
    """Best-effort replay after a number is connected. Never fails the connect."""
    pid = str(phone_number_id or "").strip()
    if not pid:
        return
    try:
        replay_unrouted(pid)
    except Exception:
        logger.exception("WhatsApp unrouted replay after connect failed phone_number_id=%s", pid)
