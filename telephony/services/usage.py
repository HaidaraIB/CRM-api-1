"""
Phone-system usage (spec section 6 ``GET /usage``; plan Phase 5 usage rollups).

``PbxUsageDaily`` is recomputed per (company, day) from ``PbxCall`` and
``PbxRecording`` (``manage.py rollup_pbx_usage``, hourly from cron): a re-run or a
late ``call.ended`` simply fixes the day. The CRM already receives every call, so
the rollup runs here rather than on the PBX side. Days are in the platform time
zone (``settings.TIME_ZONE``).

``usage_report`` is the tenant view: this month's metered minutes against the plan,
quotas against what is in use, and the last 30 days.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from ..models import PbxCall, PbxCallEvent, PbxExtension, PbxRecording, PbxTenant, PbxTrunk, PbxUsageDaily
from .emergency import numbers as emergency_numbers

MISSED = (PbxCall.Status.MISSED, PbxCall.Status.ABANDONED, PbxCall.Status.NO_ANSWER)


def _bounds(day: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


def compute_day(company_id: int, day: date) -> dict:
    start, end = _bounds(day)
    calls = PbxCall.objects.filter(company_id=company_id, started_at__gte=start, started_at__lt=end)
    agg = calls.aggregate(
        inbound=Count("id", filter=Q(direction=PbxCall.Direction.INBOUND)),
        outbound=Count("id", filter=Q(direction=PbxCall.Direction.OUTBOUND)),
        internal=Count("id", filter=Q(direction=PbxCall.Direction.INTERNAL)),
        answered=Count("id", filter=Q(status=PbxCall.Status.ANSWERED)),
        missed=Count("id", filter=Q(direction=PbxCall.Direction.INBOUND, status__in=MISSED)),
        talk=Sum("billsec_s"),
    )
    # Billed like carriers (and like monthly_pbx_minutes): every started minute of talk.
    # Emergency calls are free of the plan's minutes (services/emergency.py).
    outbound_minutes = sum(
        -(-s // 60) for s in calls.filter(direction=PbxCall.Direction.OUTBOUND, billsec_s__gt=0)
        .exclude(to_party__in=emergency_numbers())
        .values_list("billsec_s", flat=True)
    )
    recs = PbxRecording.objects.filter(company_id=company_id, started_at__gte=start, started_at__lt=end).aggregate(
        n=Count("id"), b=Sum("size_bytes")
    )
    return {
        "calls_inbound": agg["inbound"], "calls_outbound": agg["outbound"], "calls_internal": agg["internal"],
        "calls_answered": agg["answered"], "calls_missed": agg["missed"], "talk_seconds": agg["talk"] or 0,
        "outbound_minutes": outbound_minutes, "recordings": recs["n"], "recording_bytes": recs["b"] or 0,
    }


def rollup(*, days: int = 2, today: date | None = None, company_ids=None) -> int:
    """Recompute the last ``days`` days (today included) for every company with telephony."""
    today = today or timezone.localdate()
    tenants = PbxTenant.objects.all()
    if company_ids is not None:
        tenants = tenants.filter(company_id__in=company_ids)
    written = 0
    for company_id in tenants.values_list("company_id", flat=True):
        for back in range(days):
            day = today - timedelta(days=back)
            PbxUsageDaily.objects.update_or_create(company_id=company_id, day=day, defaults=compute_day(company_id, day))
            written += 1
    return written


def _limit(ent, key):
    from subscriptions.entitlements import resolve_quota_limit

    return resolve_quota_limit(ent, key)


LIMIT_HIT_DAYS = 7


def limit_hits(company_id: int, quota: str) -> dict:
    """
    Calls the node refused on a live plan limit (``quota.warning``, contract section 5)
    in the last LIMIT_HIT_DAYS days. The node reports at most one per quota every
    5 minutes, so this counts busy spells rather than single calls.
    """
    row = PbxCallEvent.objects.filter(
        company_id=company_id, type="quota.warning", data__quota=quota,
        occurred_at__gte=timezone.now() - timedelta(days=LIMIT_HIT_DAYS),
    ).aggregate(n=Count("id"), last=Max("occurred_at"))
    return {"hits_7d": row["n"], "last_hit_at": row["last"].isoformat() if row["last"] else None}


def usage_report(company) -> dict:
    from subscriptions.entitlements import (
        _parse_unlimited_int,
        build_company_entitlements,
        get_month_period_start,
        get_monthly_usage_snapshot,
    )

    from .recordings import storage_used

    ent = build_company_entitlements(company)
    period_start = get_month_period_start()
    today = timezone.localdate()
    history = {row.day: row for row in PbxUsageDaily.objects.filter(company=company, day__gte=today - timedelta(days=29))}
    days = []
    for back in range(29, -1, -1):
        day = today - timedelta(days=back)
        # Today is always live; earlier days come from the rollup (computed when missing).
        row = compute_day(company.id, day) if day == today or day not in history else {
            f: getattr(history[day], f) for f in ("calls_inbound", "calls_outbound", "calls_internal", "calls_answered",
                                                   "calls_missed", "talk_seconds", "outbound_minutes", "recordings",
                                                   "recording_bytes")
        }
        days.append({"day": day.isoformat(), **row})
    return {
        "period_start": period_start.isoformat(),
        "minutes": {
            "used": int(get_monthly_usage_snapshot(company).get("monthly_pbx_minutes", 0)),
            "limit": _parse_unlimited_int(ent.usage_limits_monthly.get("monthly_pbx_minutes")),
        },
        "extensions": {"used": PbxExtension.objects.filter(company=company).count(),
                       "limit": _limit(ent, "max_pbx_extensions")},
        "trunks": {"used": PbxTrunk.objects.filter(company=company).count(), "limit": _limit(ent, "max_pbx_trunks")},
        "recording_bytes": {"used": storage_used(company.id), "limit": _limit(ent, "max_pbx_recording_bytes")},
        "concurrent_calls": {"limit": _limit(ent, "max_pbx_concurrent_calls"),
                             **limit_hits(company.id, "max_pbx_concurrent_calls")},
        "cps": {"limit": _limit(ent, "max_pbx_cps"), **limit_hits(company.id, "max_pbx_cps")},
        "days": days,
    }
