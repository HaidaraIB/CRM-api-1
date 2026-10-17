"""Shared building blocks for report selectors."""
from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Q
from django.db.models.functions import TruncDay, TruncHour, TruncMonth, TruncWeek

from crm.models import Client
from reports.models import LeadAssignmentPeriod, LeadStatusTransition

RESPONSE_BUCKETS = [
    ("lt_5m", 0, 5 * 60),
    ("5_15m", 5 * 60, 15 * 60),
    ("15_60m", 15 * 60, 60 * 60),
    ("1_4h", 60 * 60, 4 * 3600),
    ("4_24h", 4 * 3600, 24 * 3600),
    ("gt_24h", 24 * 3600, None),
]
BUCKET_KEYS = [b[0] for b in RESPONSE_BUCKETS] + ["no_response"]


def client_attr_q(p, prefix: str = "") -> Q:
    """Filters that describe the lead itself (not who handled it)."""
    q = Q()
    if p.status_ids:
        q &= Q(**{f"{prefix}status_id__in": p.status_ids})
    if p.sources:
        q &= Q(**{f"{prefix}source__in": p.sources})
    if p.campaign_ids:
        q &= Q(**{f"{prefix}campaign_id__in": p.campaign_ids})
    if p.channel_ids:
        q &= Q(**{f"{prefix}communication_way_id__in": p.channel_ids})
    if p.lead_types:
        q &= Q(**{f"{prefix}type__in": p.lead_types})
    return q


def leads_created(p, start=None, end=None):
    qs = Client.objects.filter(company=p.company, created_at__gte=start or p.start, created_at__lt=end or p.end)
    qs = qs.filter(client_attr_q(p))
    if p.user_ids:
        qs = qs.filter(assigned_to_id__in=p.user_ids)
    return qs


def periods_in(p, start=None, end=None):
    qs = LeadAssignmentPeriod.objects.filter(
        company=p.company, assigned_at__gte=start or p.start, assigned_at__lt=end or p.end
    ).filter(client_attr_q(p, "client__"))
    if p.user_ids:
        qs = qs.filter(user_id__in=p.user_ids)
    return qs


def transitions_in(p, start=None, end=None):
    qs = LeadStatusTransition.objects.filter(
        company=p.company, changed_at__gte=start or p.start, changed_at__lt=end or p.end
    ).filter(client_attr_q(p, "client__"))
    if p.user_ids:
        qs = qs.filter(changed_by_id__in=p.user_ids)
    return qs


def percentile(sorted_values: list, q: float):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def stats(values) -> dict:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"count": 0, "avg": None, "median": None, "p90": None}
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals)),
        "median": round(percentile(vals, 0.5)),
        "p90": round(percentile(vals, 0.9)),
    }


def bucket_for(seconds):
    if seconds is None:
        return "no_response"
    for key, lo, hi in RESPONSE_BUCKETS:
        if seconds >= lo and (hi is None or seconds < hi):
            return key
    return "gt_24h"


def sla_seconds(p) -> int:
    return int(getattr(p.company, "response_sla_minutes", 15) or 15) * 60


def _trunc_fn(granularity):
    return {"hour": TruncHour, "day": TruncDay, "week": TruncWeek, "month": TruncMonth}[granularity]


def trunc(p, field_name: str):
    return _trunc_fn(p.granularity)(field_name, tzinfo=p.tz)


def _floor(dt: datetime, granularity: str) -> datetime:
    dt = dt.replace(minute=0, second=0, microsecond=0)
    if granularity == "hour":
        return dt
    dt = dt.replace(hour=0)
    if granularity == "day":
        return dt
    if granularity == "week":
        return dt - timedelta(days=dt.weekday())
    return dt.replace(day=1)


def _advance(dt: datetime, granularity: str) -> datetime:
    if granularity == "hour":
        return dt + timedelta(hours=1)
    if granularity == "day":
        return dt + timedelta(days=1)
    if granularity == "week":
        return dt + timedelta(days=7)
    return (dt.replace(day=28) + timedelta(days=4)).replace(day=1)


def fill_series(p, rows: dict, empty: dict) -> list[dict]:
    """
    ``rows`` maps a DB-truncated bucket datetime -> metrics dict. Returns one entry per bucket across
    the whole period (zero-filled), keyed by the bucket's company-local start as an ISO string.
    """
    by_key = {_floor(k.astimezone(p.tz), p.granularity).replace(tzinfo=None): v for k, v in rows.items()}
    cur = _floor(p.start.astimezone(p.tz), p.granularity).replace(tzinfo=None)
    stop = p.end.astimezone(p.tz).replace(tzinfo=None)
    out = []
    while cur < stop and len(out) < 2000:
        out.append({"bucket": cur.isoformat(), **{**empty, **by_key.get(cur, {})}})
        cur = _advance(cur, p.granularity)
    return out


def data_quality(p) -> dict:
    """How much of the requested period rests on reconstructed (backfilled) history."""
    live = (
        LeadStatusTransition.objects.filter(company=p.company, is_backfilled=False)
        .order_by("changed_at")
        .values_list("changed_at", flat=True)
        .first()
    )
    total = transitions_in(p).count()
    backfilled = transitions_in(p).filter(is_backfilled=True).count()
    return {
        "live_history_since": live.isoformat() if live else None,
        "backfilled_share": round(backfilled / total, 4) if total else 0,
        "has_backfilled": backfilled > 0,
    }
