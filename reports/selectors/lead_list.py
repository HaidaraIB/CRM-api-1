"""Paginated drill-down: the leads behind any number on the reports hub."""
from collections import defaultdict

from django.db.models import Q

from crm.models import Client

from .base import RESPONSE_BUCKETS, client_attr_q, leads_created, periods_in, transitions_in

MAX_PAGE_SIZE = 100
METRICS = ("created", "status_entered", "status_left", "responded", "unanswered", "response_bucket", "unassigned")


def _bucket_q(key):
    for k, lo, hi in RESPONSE_BUCKETS:
        if k == key:
            q = Q(business_response_seconds__gte=lo)
            return q & Q(business_response_seconds__lt=hi) if hi is not None else q
    return None


def _client_ids(p, metric, status_id, bucket):
    if metric == "created":
        return leads_created(p).values("id")
    if metric == "unassigned":
        return leads_created(p).filter(assigned_to__isnull=True).values("id")
    if metric == "status_entered":
        return transitions_in(p).filter(to_status_id=status_id).values("client_id")
    if metric == "status_left":
        return transitions_in(p).filter(from_status_id=status_id).values("client_id")
    periods = periods_in(p)
    if metric == "responded":
        return periods.filter(first_response_at__isnull=False).values("client_id")
    if metric == "unanswered":
        return periods.filter(first_response_at__isnull=True, ended_at__isnull=True).values("client_id")
    if metric == "response_bucket":
        if bucket == "no_response":
            return periods.filter(first_response_at__isnull=True).values("client_id")
        q = _bucket_q(bucket)
        return periods.filter(q, first_response_at__isnull=False).values("client_id") if q else None
    return None


def build(p, request) -> dict:
    q = request.query_params
    metric = (q.get("metric") or "created").strip()
    if metric not in METRICS:
        metric = "created"
    status_id = int(q["status_id"]) if (q.get("status_id") or "").isdigit() else None
    ids = _client_ids(p, metric, status_id, (q.get("bucket") or "").strip())
    if ids is None:
        return {"count": 0, "page": 1, "page_size": 25, "results": []}

    qs = Client.objects.filter(company=p.company, id__in=ids).select_related("status", "assigned_to", "campaign")
    search = (q.get("search") or "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(phone_number__icontains=search))
    # status/source/etc. filters describe the lead; apply them even for drill-downs not built from leads_created
    qs = qs.filter(client_attr_q(p))

    page = max(1, int(q["page"])) if (q.get("page") or "").isdigit() else 1
    size = min(MAX_PAGE_SIZE, max(1, int(q["page_size"]))) if (q.get("page_size") or "").isdigit() else 25
    total = qs.count()
    leads = list(qs.order_by("-created_at", "-id")[(page - 1) * size : page * size])
    lead_ids = [c.id for c in leads]

    path = defaultdict(list)
    for cid, name in transitions_in(p).filter(client_id__in=lead_ids).order_by("changed_at", "id").values_list(
        "client_id", "to_status_name"
    ):
        path[cid].append(name)
    response = {}
    for a in periods_in(p).filter(client_id__in=lead_ids).select_related("user").order_by("assigned_at"):
        response[a.client_id] = a

    results = []
    for c in leads:
        a = response.get(c.id)
        results.append(
            {
                "id": c.id,
                "name": c.name,
                "phone_number": c.phone_number,
                "status": c.status.name if c.status else None,
                "assigned_to": (c.assigned_to.get_full_name() or c.assigned_to.username) if c.assigned_to else None,
                "source": c.source,
                "campaign": c.campaign.name if c.campaign else None,
                "created_at": c.created_at.isoformat(),
                "status_path": path.get(c.id, []),
                "first_response_at": a.first_response_at.isoformat() if a and a.first_response_at else None,
                "first_response_kind": a.first_response_kind if a else None,
                "response_seconds": a.response_seconds if a else None,
                "business_response_seconds": a.business_response_seconds if a else None,
            }
        )
    return {"count": total, "page": page, "page_size": size, "results": results}
