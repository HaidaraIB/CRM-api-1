"""Unified outbound campaign message log feed for Messaging Center."""

from __future__ import annotations

from datetime import datetime, time

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from integrations.models import (
    LeadSMSMessage,
    LeadWhatsAppMessage,
    MessageCampaignBatch,
    MessageCampaignFailure,
    MessageSendSource,
)


def _end_of_day(d):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(d, time(23, 59, 59, 999000)), tz)


def _parse_dt(value: str, *, end_of_day: bool = False):
    if not value:
        return None
    dt = parse_datetime(value)
    if dt:
        return dt
    d = parse_date(value)
    if not d:
        return None
    if end_of_day:
        return _end_of_day(d)
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(d, time.min), tz)


def _body_preview(body: str, limit: int = 160) -> str:
    text = (body or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_campaign_status(channel: str, raw_status: str | None) -> str:
    """Campaign delivery statuses: pending, sent, delivered, failed."""
    value = (raw_status or "").lower()
    if value == "failed":
        return "failed"
    if channel == "sms":
        return "sent"
    if value in ("delivered", "read"):
        return "delivered"
    if value == "sent" or not value:
        return "pending"
    return "pending"


def _sms_to_log(msg: LeadSMSMessage) -> dict:
    client = msg.client
    batch_id = msg.campaign_batch_id
    return {
        "id": f"sms-{msg.pk}",
        "source_id": msg.pk,
        "channel": "sms",
        "campaign_batch_id": batch_id,
        "client_id": msg.client_id,
        "client_name": getattr(client, "name", "") or "",
        "phone_number": msg.phone_number or "",
        "body": msg.body or "",
        "body_preview": _body_preview(msg.body or ""),
        "direction": "outbound",
        "status": "sent",
        "error": None,
        "provider": msg.provider or "",
        "external_id": msg.external_message_id or msg.twilio_sid or "",
        "created_by_username": (
            "" if msg.created_by_id is None else getattr(msg.created_by, "username", "") or ""
        ),
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


def _wa_to_log(msg: LeadWhatsAppMessage) -> dict:
    client = msg.client
    status = _normalize_campaign_status("whatsapp", msg.delivery_status)
    return {
        "id": f"wa-{msg.pk}",
        "source_id": msg.pk,
        "channel": "whatsapp",
        "campaign_batch_id": msg.campaign_batch_id,
        "client_id": msg.client_id,
        "client_name": getattr(client, "name", "") or "",
        "phone_number": msg.phone_number or "",
        "body": msg.body or "",
        "body_preview": _body_preview(msg.body or ""),
        "direction": "outbound",
        "status": status,
        "error": msg.delivery_error or None,
        "provider": "whatsapp",
        "external_id": msg.whatsapp_message_id or "",
        "created_by_username": (
            "" if msg.created_by_id is None else getattr(msg.created_by, "username", "") or ""
        ),
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


def _failure_to_log(row: MessageCampaignFailure) -> dict:
    client = row.client
    channel = row.batch.channel if row.batch_id else "sms"
    return {
        "id": f"fail-{row.pk}",
        "source_id": row.pk,
        "channel": channel,
        "campaign_batch_id": row.batch_id,
        "client_id": row.client_id,
        "client_name": getattr(client, "name", "") or "" if client else "",
        "phone_number": row.phone_number or "",
        "body": "",
        "body_preview": "",
        "direction": "outbound",
        "status": "failed",
        "error": row.error or "",
        "provider": channel,
        "external_id": "",
        "created_by_username": "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _apply_search(qs, search: str):
    if not search:
        return qs
    return qs.filter(
        Q(phone_number__icontains=search)
        | Q(body__icontains=search)
        | Q(client__name__icontains=search)
    )


def _apply_date(qs, date_from, date_to):
    if date_from:
        qs = qs.filter(created_at__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__lte=date_to)
    return qs


def _matches_status(entry: dict, status: str) -> bool:
    if status == "all":
        return True
    return entry.get("status") == status


def fetch_message_logs(company, params) -> dict:
    """Return paginated outbound campaign message log entries."""
    channel = (params.get("channel") or "all").strip().lower()
    status = (params.get("status") or "all").strip().lower()
    search = (params.get("search") or "").strip()
    client_id = (params.get("client") or "").strip()
    batch_id = (params.get("batch") or "").strip()
    date_from = _parse_dt((params.get("date_from") or "").strip())
    date_to = _parse_dt((params.get("date_to") or "").strip(), end_of_day=True)

    try:
        page = max(1, int(params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int(params.get("page_size", 30))))
    except (TypeError, ValueError):
        page_size = 30

    include_sms = channel in ("all", "sms")
    include_wa = channel in ("all", "whatsapp")

    sms_qs = LeadSMSMessage.objects.filter(
        client__company=company,
        send_source=MessageSendSource.CAMPAIGN,
        direction=LeadSMSMessage.DIRECTION_OUTBOUND,
    ).select_related("client", "created_by", "campaign_batch")

    wa_qs = LeadWhatsAppMessage.objects.filter(
        client__company=company,
        send_source=MessageSendSource.CAMPAIGN,
        direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
    ).select_related("client", "created_by", "campaign_batch")

    fail_qs = MessageCampaignFailure.objects.filter(batch__company=company).select_related(
        "client", "batch"
    )

    if not include_sms:
        sms_qs = sms_qs.none()
    if not include_wa:
        wa_qs = wa_qs.none()
        fail_qs = fail_qs.exclude(batch__channel=MessageCampaignBatch.CHANNEL_WHATSAPP)
    if channel == "sms":
        fail_qs = fail_qs.filter(batch__channel=MessageCampaignBatch.CHANNEL_SMS)

    if client_id and str(client_id).isdigit():
        cid = int(client_id)
        sms_qs = sms_qs.filter(client_id=cid)
        wa_qs = wa_qs.filter(client_id=cid)
        fail_qs = fail_qs.filter(client_id=cid)

    if batch_id and str(batch_id).isdigit():
        bid = int(batch_id)
        sms_qs = sms_qs.filter(campaign_batch_id=bid)
        wa_qs = wa_qs.filter(campaign_batch_id=bid)
        fail_qs = fail_qs.filter(batch_id=bid)

    sms_qs = _apply_search(sms_qs, search)
    wa_qs = _apply_search(wa_qs, search)
    fail_qs = _apply_date(fail_qs, date_from, date_to)
    if search:
        fail_qs = fail_qs.filter(
            Q(phone_number__icontains=search)
            | Q(client__name__icontains=search)
            | Q(error__icontains=search)
        )

    sms_qs = _apply_date(sms_qs, date_from, date_to)
    wa_qs = _apply_date(wa_qs, date_from, date_to)

    all_entries: list[dict] = []
    all_entries.extend(_sms_to_log(m) for m in sms_qs.order_by("-created_at"))
    all_entries.extend(_wa_to_log(m) for m in wa_qs.order_by("-created_at"))
    all_entries.extend(_failure_to_log(f) for f in fail_qs.order_by("-created_at"))

    if status != "all":
        all_entries = [e for e in all_entries if _matches_status(e, status)]

    all_entries.sort(key=lambda row: row["created_at"] or "", reverse=True)
    total = len(all_entries)

    start = (page - 1) * page_size
    results = all_entries[start : start + page_size]

    summary = {
        "total": len(all_entries),
        "pending": sum(1 for e in all_entries if e["status"] == "pending"),
        "sent": sum(1 for e in all_entries if e["status"] == "sent"),
        "delivered": sum(1 for e in all_entries if e["status"] == "delivered"),
        "failed": sum(1 for e in all_entries if e["status"] == "failed"),
        "sms": sum(1 for e in all_entries if e["channel"] == "sms"),
        "whatsapp": sum(1 for e in all_entries if e["channel"] == "whatsapp"),
    }

    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "summary": summary,
        "results": results,
    }
