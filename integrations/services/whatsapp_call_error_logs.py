"""WhatsApp Cloud Calling error log — owner-only Messaging Center feed."""

from __future__ import annotations

import logging
from datetime import datetime, time

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from integrations.models import WhatsAppCallErrorLog, WhatsAppCallErrorSource

logger = logging.getLogger(__name__)


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


def log_whatsapp_call_error(
    *,
    company,
    source: str,
    error_code: str = "",
    error_message: str = "",
    agent=None,
    client=None,
    peer_phone: str = "",
    whatsapp_account=None,
    whatsapp_call=None,
    meta_details: dict | None = None,
) -> WhatsAppCallErrorLog | None:
    """Persist a call failure for the owner Call Error Logs feed. Never raises."""
    if not company:
        return None
    try:
        src = (source or "").strip().lower()
        valid = {c.value for c in WhatsAppCallErrorSource}
        if src not in valid:
            src = WhatsAppCallErrorSource.WEBRTC.value
        return WhatsAppCallErrorLog.objects.create(
            company=company,
            whatsapp_account=whatsapp_account,
            whatsapp_call=whatsapp_call,
            agent=agent if getattr(agent, "pk", None) else None,
            client=client if getattr(client, "pk", None) else None,
            peer_phone="".join(c for c in (peer_phone or "") if c.isdigit())[:32],
            source=src,
            error_code=(error_code or "")[:128],
            error_message=(error_message or "")[:4000],
            meta_details=meta_details if isinstance(meta_details, dict) else {},
        )
    except Exception:
        logger.exception("Failed to persist WhatsApp call error log")
        return None


def _serialize_entry(row: WhatsAppCallErrorLog) -> dict:
    client = row.client
    agent = row.agent
    return {
        "id": row.pk,
        "source": row.source,
        "error_code": row.error_code or "",
        "error_message": row.error_message or "",
        "peer_phone": row.peer_phone or "",
        "client_id": row.client_id,
        "client_name": getattr(client, "name", "") or "" if client else "",
        "agent_id": row.agent_id,
        "agent_username": getattr(agent, "username", "") or "" if agent else "",
        "whatsapp_account_id": row.whatsapp_account_id,
        "whatsapp_call_id": row.whatsapp_call_id,
        "meta_details": row.meta_details or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def fetch_call_error_logs(company, params) -> dict:
    """Paginated owner-only call error log feed."""
    source = (params.get("source") or "all").strip().lower()
    search = (params.get("search") or "").strip()
    error_code = (params.get("error_code") or "").strip()
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

    qs = WhatsAppCallErrorLog.objects.filter(company=company).select_related(
        "client", "agent", "whatsapp_account"
    )
    if source and source != "all":
        qs = qs.filter(source=source)
    if error_code:
        qs = qs.filter(error_code__icontains=error_code)
    if search:
        qs = qs.filter(
            Q(peer_phone__icontains=search)
            | Q(error_message__icontains=search)
            | Q(error_code__icontains=search)
            | Q(client__name__icontains=search)
            | Q(agent__username__icontains=search)
        )
    if date_from:
        qs = qs.filter(created_at__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__lte=date_to)

    total = qs.count()
    by_source = {
        row["source"]: row["c"]
        for row in qs.values("source").annotate(c=Count("id"))
    }
    start = (page - 1) * page_size
    rows = list(qs.order_by("-created_at")[start : start + page_size])
    results = [_serialize_entry(r) for r in rows]

    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "summary": {
            "total": total,
            "by_source": by_source,
            "initiate": by_source.get(WhatsAppCallErrorSource.INITIATE.value, 0),
            "permission_request": by_source.get(
                WhatsAppCallErrorSource.PERMISSION_REQUEST.value, 0
            ),
            "mic": by_source.get(WhatsAppCallErrorSource.MIC.value, 0),
            "webhook": by_source.get(WhatsAppCallErrorSource.WEBHOOK.value, 0),
            "out_of_hours": by_source.get(WhatsAppCallErrorSource.OUT_OF_HOURS.value, 0),
        },
        "results": results,
    }
