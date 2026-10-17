from django.db.models import Avg, Count, Sum

from crm.deals.legacy import won_deals_q
from crm.models import Campaign, Deal
from reports.models import LeadAssignmentPeriod

from .base import leads_created


def _grouped(p, leads, key, label_field=None):
    lead_rows = {
        r[key]: r
        for r in leads.values(*([key, label_field] if label_field else [key])).annotate(n=Count("id"))
    }
    won = {
        r["client__" + key]: r
        for r in Deal.objects.filter(company=p.company, client__in=leads)
        .filter(won_deals_q())
        .values("client__" + key)
        .annotate(deals=Count("id"), converted=Count("client", distinct=True), revenue=Sum("value"))
    }
    response = {
        r["client__" + key]: r["avg"]
        for r in LeadAssignmentPeriod.objects.filter(
            company=p.company, client__in=leads, first_response_at__isnull=False
        )
        .values("client__" + key)
        .annotate(avg=Avg("business_response_seconds"))
    }
    out = []
    for k, r in lead_rows.items():
        w = won.get(k)
        n = r["n"]
        out.append(
            {
                "key": k,
                "label": (r.get(label_field) if label_field else k) or "",
                "leads": n,
                "converted_leads": w["converted"] if w else 0,
                "conversion_rate": round(100 * w["converted"] / n, 1) if w and n else 0.0,
                "deals_won": w["deals"] if w else 0,
                "revenue": float(w["revenue"] or 0) if w else 0.0,
                "avg_business_response_seconds": round(response[k]) if response.get(k) is not None else None,
            }
        )
    out.sort(key=lambda r: -r["leads"])
    return out


def build(p) -> dict:
    leads = leads_created(p)
    by_source = _grouped(p, leads, "source")
    by_campaign = _grouped(p, leads.filter(campaign__isnull=False), "campaign_id", "campaign__name")
    budgets = {c.id: c.budget for c in Campaign.objects.filter(company=p.company)}
    for row in by_campaign:
        budget = budgets.get(row["key"])
        row["budget"] = float(budget) if budget is not None else None
        row["cost_per_lead"] = round(float(budget) / row["leads"], 2) if budget and row["leads"] else None
    return {"by_source": by_source, "by_campaign": by_campaign}
