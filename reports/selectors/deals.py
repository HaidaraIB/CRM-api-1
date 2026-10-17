from django.db.models import Avg, Count, ExpressionWrapper, F, DurationField, Q, Sum

from crm.deals.legacy import won_deals_q
from crm.models import Deal, DealEvent

from .base import client_attr_q, fill_series, trunc


def _scoped(p, **filters):
    qs = Deal.objects.filter(company=p.company, **filters).filter(client_attr_q(p, "client__"))
    if p.user_ids:
        qs = qs.filter(employee_id__in=p.user_ids)
    return qs


def lost_deals_q():
    return Q(pipeline_stage__stage_type="lost") | Q(stage__iexact="lost")


def build(p) -> dict:
    created = _scoped(p, created_at__gte=p.start, created_at__lt=p.end)
    won = _scoped(p, won_at__gte=p.start, won_at__lt=p.end).filter(won_deals_q())
    lost = _scoped(p, lost_at__gte=p.start, lost_at__lt=p.end).filter(lost_deals_q())

    won_agg = won.aggregate(n=Count("id"), revenue=Sum("value"))
    lost_n = lost.count()
    cycle = won.exclude(won_at__isnull=True).annotate(
        d=ExpressionWrapper(F("won_at") - F("created_at"), output_field=DurationField())
    ).aggregate(a=Avg("d"))["a"]

    won_rows = {r["b"]: {"won": r["n"], "revenue": float(r["rev"] or 0)}
                for r in won.annotate(b=trunc(p, "won_at")).values("b").annotate(n=Count("id"), rev=Sum("value"))}
    stage_moves = (
        DealEvent.objects.filter(
            deal__company=p.company, event_type="stage_change", created_at__gte=p.start, created_at__lt=p.end
        )
        .values("new_value")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    by_employee = (
        won.values("employee_id", "employee__first_name", "employee__last_name", "employee__username")
        .annotate(n=Count("id"), rev=Sum("value"))
        .order_by("-rev")
    )
    return {
        "summary": {
            "created": created.count(),
            "won": won_agg["n"] or 0,
            "lost": lost_n,
            "revenue": float(won_agg["revenue"] or 0),
            "win_rate": round(100 * (won_agg["n"] or 0) / ((won_agg["n"] or 0) + lost_n), 1)
            if (won_agg["n"] or 0) + lost_n
            else None,
            "avg_cycle_days": round(cycle.total_seconds() / 86400, 1) if cycle else None,
        },
        "series": fill_series(p, won_rows, {"won": 0, "revenue": 0.0}),
        "lost_reasons": [
            {"reason": r["lost_reason__name"] or "—", "count": r["n"]}
            for r in lost.values("lost_reason__name").annotate(n=Count("id")).order_by("-n")
        ],
        "stage_movement": [{"stage": r["new_value"] or "—", "count": r["n"]} for r in stage_moves],
        "by_employee": [
            {
                "user_id": r["employee_id"],
                "name": f"{r['employee__first_name'] or ''} {r['employee__last_name'] or ''}".strip()
                or r["employee__username"],
                "won": r["n"],
                "revenue": float(r["rev"] or 0),
            }
            for r in by_employee
        ],
    }
