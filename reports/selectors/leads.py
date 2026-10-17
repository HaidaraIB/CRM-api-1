from django.db.models import Count, F, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from crm.models import Client
from reports.models import LeadStatusTransition
from settings.models import LeadStatus

from .base import client_attr_q, fill_series, leads_created, periods_in, trunc


def _breakdown(qs, field, name_field=None, limit=50):
    rows = qs.values(field, *( [name_field] if name_field else [])).annotate(n=Count("id")).order_by("-n")[:limit]
    return [
        {"key": r[field], "label": (r[name_field] if name_field else r[field]) or "", "count": r["n"]} for r in rows
    ]


def status_distribution_as_of(p, as_of=None) -> list[dict]:
    """
    Where every lead stood at ``as_of`` (default: period end), from the last transition at or before that
    moment. Leads with no recorded history fall back to their current status.
    """
    as_of = as_of or p.end
    last = (
        LeadStatusTransition.objects.filter(client=OuterRef("pk"), changed_at__lt=as_of)
        .order_by("-changed_at", "-id")
        .values("to_status_id")[:1]
    )
    qs = (
        Client.objects.filter(company=p.company, created_at__lt=as_of)
        .filter(client_attr_q(p))
        .annotate(_asof=Coalesce(Subquery(last), F("status_id"), output_field=IntegerField()))
    )
    if p.user_ids:
        qs = qs.filter(assigned_to_id__in=p.user_ids)
    counts = {r["_asof"]: r["n"] for r in qs.values("_asof").annotate(n=Count("id"))}
    statuses = {s.id: s for s in LeadStatus.objects.filter(company=p.company)}
    out = []
    for sid, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        s = statuses.get(sid)
        out.append(
            {
                "status_id": sid,
                "status": s.name if s else "—",
                "color": s.color if s else None,
                "category": s.category if s else None,
                "count": n,
            }
        )
    return out


def build(p) -> dict:
    leads = leads_created(p)
    series_rows = {
        r["b"]: {"new_leads": r["n"]} for r in leads.annotate(b=trunc(p, "created_at")).values("b").annotate(n=Count("id"))
    }
    touched_ids = set(
        periods_in(p).filter(first_response_at__isnull=False).values_list("client_id", flat=True).distinct()
    )
    total = leads.count()
    assigned = leads.filter(assigned_to__isnull=False).count()
    return {
        "total": total,
        "series": fill_series(p, series_rows, {"new_leads": 0}),
        "funnel": {
            "created": total,
            "assigned": assigned,
            "touched": leads.filter(id__in=touched_ids).count(),
            "untouched": total - leads.filter(id__in=touched_ids).count(),
        },
        "by_source": _breakdown(leads, "source"),
        "by_campaign": _breakdown(leads.filter(campaign__isnull=False), "campaign_id", "campaign__name"),
        "by_channel": _breakdown(leads.filter(communication_way__isnull=False), "communication_way_id", "communication_way__name"),
        "by_type": _breakdown(leads, "type"),
        "by_assignee": [
            {"key": r["assigned_to_id"], "label": r["assigned_to__username"] or "", "count": r["n"]}
            for r in leads.filter(assigned_to__isnull=False)
            .values("assigned_to_id", "assigned_to__username")
            .annotate(n=Count("id"))
            .order_by("-n")
        ],
        "unassigned": total - assigned,
        "status_as_of_end": status_distribution_as_of(p),
        "status_as_of_start": status_distribution_as_of(p, p.start) if p.compare else None,
    }
