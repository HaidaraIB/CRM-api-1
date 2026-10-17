from django.db.models import Count, Exists, OuterRef, Subquery, Sum

from crm.deals.legacy import won_deals_q
from crm.models import Client, ClientCall, Deal
from integrations.models import LeadWhatsAppMessage

from .base import client_attr_q, fill_series, leads_created, periods_in, sla_seconds, stats, transitions_in, trunc


def _won_deals(p, start, end):
    qs = Deal.objects.filter(company=p.company, won_at__gte=start, won_at__lt=end).filter(won_deals_q())
    qs = qs.filter(client_attr_q(p, "client__"))
    if p.user_ids:
        qs = qs.filter(employee_id__in=p.user_ids)
    return qs


def unanswered_customer_messages(p, start, end) -> int:
    last_direction = (
        LeadWhatsAppMessage.objects.filter(client=OuterRef("pk")).order_by("-created_at", "-pk").values("direction")[:1]
    )
    inbound_in_range = LeadWhatsAppMessage.objects.filter(
        client=OuterRef("pk"),
        direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        created_at__gte=start,
        created_at__lt=end,
    )
    qs = Client.objects.filter(company=p.company).filter(client_attr_q(p))
    if p.user_ids:
        qs = qs.filter(assigned_to_id__in=p.user_ids)
    return (
        qs.annotate(_last=Subquery(last_direction), _has=Exists(inbound_in_range))
        .filter(_has=True, _last=LeadWhatsAppMessage.DIRECTION_INBOUND)
        .count()
    )


def metrics_for(p, start, end) -> dict:
    new_leads = leads_created(p, start, end).count()
    periods = list(periods_in(p, start, end).values("first_response_at", "response_seconds", "business_response_seconds",
                                                   "assigned_at", "ended_at"))
    responded = [r for r in periods if r["first_response_at"] is not None]
    raw = stats(r["response_seconds"] for r in responded)
    biz = stats(r["business_response_seconds"] for r in responded)

    sla = sla_seconds(p)
    from django.utils import timezone

    now = timezone.now()
    within = sum(1 for r in responded if (r["business_response_seconds"] if r["business_response_seconds"] is not None
                                          else r["response_seconds"]) <= sla)
    breached_unanswered = sum(
        1 for r in periods
        if r["first_response_at"] is None and ((r["ended_at"] or now) - r["assigned_at"]).total_seconds() > sla
    )
    denominator = len(responded) + breached_unanswered

    calls = ClientCall.objects.filter(client__company=p.company, created_at__gte=start, created_at__lt=end).filter(
        client_attr_q(p, "client__")
    )
    if p.user_ids:
        calls = calls.filter(created_by_id__in=p.user_ids)

    won = _won_deals(p, start, end)
    won_agg = won.aggregate(n=Count("id"), revenue=Sum("value"))
    return {
        "new_leads": new_leads,
        "assignments": len(periods),
        "leads_worked": len(responded),
        "response_time": {"wall": raw, "business": biz},
        "within_sla_pct": round(100 * within / denominator, 1) if denominator else None,
        "sla_seconds": sla,
        "status_changes": transitions_in(p, start, end).exclude(from_status_name="", from_status__isnull=True).count(),
        "calls": calls.count(),
        "deals_won": won_agg["n"] or 0,
        "revenue": float(won_agg["revenue"] or 0),
        "conversion_rate": round(100 * (won_agg["n"] or 0) / new_leads, 1) if new_leads else None,
        "unanswered_customer_messages": unanswered_customer_messages(p, start, end),
    }


def build(p) -> dict:
    out = {"current": metrics_for(p, p.start, p.end), "previous": None}
    if p.compare:
        ps, pe = p.previous
        out["previous"] = metrics_for(p, ps, pe)
        out["previous_range"] = {"start": ps.isoformat(), "end": pe.isoformat()}
    rows = {
        r["b"]: {"new_leads": r["n"]}
        for r in leads_created(p).annotate(b=trunc(p, "created_at")).values("b").annotate(n=Count("id"))
    }
    out["series"] = fill_series(p, rows, {"new_leads": 0})
    return out
