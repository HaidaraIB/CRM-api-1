"""
Platform (admin panel) dashboard aggregates.

Formulas mirror CRM-admin-panel pages/Dashboard.tsx loadDashboardData so KPIs
are correct beyond the first list page and load without downloading all rows.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from companies.models import Company
from subscriptions.models import Payment, PaymentStatus, Plan, Subscription

SUCCESSFUL_PAYMENT_STATUSES = (
    PaymentStatus.COMPLETED.value,
    "successful",
    "success",
)


def _parse_date(value: str | None, *, end_of_day: bool = False):
    if not value:
        return None
    try:
        raw = str(value).strip()[:10]
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    wall = time.max if end_of_day else time.min
    return timezone.make_aware(datetime.combine(day, wall))


def _default_range():
    """Match admin FE getDefaultDateRange: start = first day of (now-11 months), end = today."""
    now = timezone.localtime()
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    # First day of the month 11 months before the current month
    month_index = now.year * 12 + (now.month - 1) - 11
    start_year, start_month0 = divmod(month_index, 12)
    start = timezone.make_aware(
        datetime(start_year, start_month0 + 1, 1, 0, 0, 0)
    )
    return start, end


def _payment_amount_expr():
    return Coalesce("amount_usd", "amount", Value(Decimal("0")))


def build_platform_dashboard_summary(
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    range_start = _parse_date(start, end_of_day=False)
    range_end = _parse_date(end, end_of_day=True)
    if range_start is None or range_end is None or range_start > range_end:
        range_start, range_end = _default_range()

    today_local = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today_local, time.min))
    today_end = timezone.make_aware(datetime.combine(today_local, time.max))
    mrr_start = today_start - timedelta(days=30)
    new_subs_start = range_end - timedelta(days=30)

    successful_q = Q(payment_status__in=SUCCESSFUL_PAYMENT_STATUSES)

    # --- KPIs ---
    mrr_agg = (
        Payment.objects.filter(successful_q)
        .filter(created_at__gte=mrr_start, created_at__lte=today_end)
        .aggregate(total=Coalesce(Sum(_payment_amount_expr()), Value(Decimal("0"))))
    )
    mrr = float(mrr_agg["total"] or 0)

    active_tenants = Subscription.objects.filter(is_active=True).count()

    new_subscriptions = Subscription.objects.filter(
        created_at__gte=new_subs_start,
        created_at__lte=range_end,
    ).count()

    expiring_subscriptions = Subscription.objects.filter(
        is_active=True,
        end_date__date__gte=today_local,
        end_date__date__lte=(today_local + timedelta(days=7)),
    ).count()

    # --- Revenue by month within selected range ---
    # Build month sequence (max 12 months, same as FE buildMonthSequence)
    months: list[dict[str, Any]] = []
    cursor = datetime(range_start.year, range_start.month, 1)
    end_month = datetime(range_end.year, range_end.month, 1)
    guard = 0
    while cursor <= end_month and guard < 60:
        months.append({"year": cursor.year, "month": cursor.month - 1, "revenue": 0.0})
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1)
        guard += 1
    if len(months) > 12:
        months = months[-12:]

    month_index = {(m["year"], m["month"]): m for m in months}
    revenue_rows = (
        Payment.objects.filter(successful_q)
        .filter(created_at__gte=range_start, created_at__lte=range_end)
        .annotate(bucket=TruncMonth("created_at", tzinfo=timezone.get_current_timezone()))
        .values("bucket")
        .annotate(total=Coalesce(Sum(_payment_amount_expr()), Value(Decimal("0"))))
    )
    for row in revenue_rows:
        bucket = row["bucket"]
        if not bucket:
            continue
        local_bucket = timezone.localtime(bucket) if timezone.is_aware(bucket) else bucket
        key = (local_bucket.year, local_bucket.month - 1)
        if key in month_index:
            month_index[key]["revenue"] = float(row["total"] or 0)

    # --- Plan distribution: one count per company with an active subscription ---
    plans = list(Plan.objects.order_by("id").values("id", "name", "name_ar"))
    plan_by_id = {p["id"]: p for p in plans}
    plan_counts = {p["id"]: 0 for p in plans}

    active_subs = (
        Subscription.objects.filter(is_active=True)
        .order_by("company_id", "-created_at")
        .values("company_id", "plan_id")
    )
    seen_companies: set[int] = set()
    for row in active_subs:
        cid = row["company_id"]
        if cid in seen_companies:
            continue
        seen_companies.add(cid)
        pid = row["plan_id"]
        if pid in plan_counts:
            plan_counts[pid] += 1

    plan_distribution = [
        {
            "plan_id": p["id"],
            "name": p["name"],
            "name_ar": p["name_ar"] or "",
            "count": plan_counts[p["id"]],
        }
        for p in plans
    ]

    # --- Recent companies (top 5) ---
    recent_company_rows = list(
        Company.objects.order_by("-created_at").values("id", "name")[:5]
    )
    company_ids = [r["id"] for r in recent_company_rows]
    active_for_recent: dict[int, int] = {}
    for row in (
        Subscription.objects.filter(is_active=True, company_id__in=company_ids)
        .order_by("company_id", "-created_at")
        .values("company_id", "plan_id")
    ):
        if row["company_id"] not in active_for_recent:
            active_for_recent[row["company_id"]] = row["plan_id"]

    recent_companies = []
    for row in recent_company_rows:
        plan = plan_by_id.get(active_for_recent.get(row["id"]))
        recent_companies.append(
            {
                "name": row["name"],
                "plan_name": plan["name"] if plan else None,
                "plan_name_ar": (plan["name_ar"] if plan else None) or "",
            }
        )

    # --- Recent successful payments (top 5) ---
    recent_payment_rows = (
        Payment.objects.filter(successful_q)
        .select_related("subscription__company")
        .order_by("-created_at")[:5]
    )
    recent_payments = []
    for p in recent_payment_rows:
        amount = p.amount_usd if p.amount_usd is not None else p.amount
        company_name = ""
        if p.subscription_id and p.subscription.company_id:
            company_name = p.subscription.company.name
        recent_payments.append(
            {
                "company_name": company_name,
                "amount_usd": float(amount or 0),
            }
        )

    return {
        "mrr": mrr,
        "active_tenants": active_tenants,
        "new_subscriptions": new_subscriptions,
        "expiring_subscriptions": expiring_subscriptions,
        "revenue_by_month": months,
        "plan_distribution": plan_distribution,
        "recent_companies": recent_companies,
        "recent_payments": recent_payments,
        "start": range_start.date().isoformat(),
        "end": range_end.date().isoformat(),
    }
