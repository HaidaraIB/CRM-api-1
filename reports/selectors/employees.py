from collections import defaultdict

from django.db.models import Count, Sum

from accounts.models import Role, User, WorkDaySummary
from crm.deals.legacy import won_deals_q
from crm.models import ClientCall, ClientFieldVisit, ClientTask, ClientVisit, Deal
from integrations.models import LeadSMSMessage, LeadWhatsAppMessage

from .base import client_attr_q, periods_in, stats, transitions_in

STAFF_ROLES = [Role.ADMIN.value, Role.SUPERVISOR.value, Role.EMPLOYEE.value, Role.RECEPTION.value, Role.DOCTOR.value]


def classify_call(method_name: str) -> str:
    """Same rule as crm.report_metrics._classify_call, on the method name only."""
    m = (method_name or "").lower()
    if "no answer" in m or "not answered" in m:
        return "missed"
    if "answered" in m or "following" in m:
        return "answered"
    return "unknown"


def _count_by(qs, field) -> dict:
    return {r[field]: r["n"] for r in qs.values(field).annotate(n=Count("id")) if r[field] is not None}


def build(p) -> dict:
    users = User.objects.filter(company=p.company, is_active=True, role__in=STAFF_ROLES)
    if p.user_ids:
        users = users.filter(id__in=p.user_ids)
    users = list(users.order_by("first_name", "last_name", "username"))

    attr = client_attr_q(p, "client__")
    in_range = {"created_at__gte": p.start, "created_at__lt": p.end}

    periods = defaultdict(list)
    for r in periods_in(p).values("user_id", "first_response_at", "response_seconds", "business_response_seconds"):
        periods[r["user_id"]].append(r)

    call_counts = defaultdict(lambda: {"total": 0, "answered": 0, "missed": 0, "unknown": 0})
    for uid, method in ClientCall.objects.filter(client__company=p.company, **in_range).filter(attr).values_list(
        "created_by_id", "call_method__name"
    ):
        if uid is None:
            continue
        c = call_counts[uid]
        c["total"] += 1
        c[classify_call(method)] += 1

    tasks = _count_by(ClientTask.objects.filter(client__company=p.company, **in_range).filter(attr), "created_by_id")
    visits = _count_by(ClientVisit.objects.filter(client__company=p.company, **in_range).filter(attr), "created_by_id")
    for k, v in _count_by(
        ClientFieldVisit.objects.filter(client__company=p.company, **in_range).filter(attr), "created_by_id"
    ).items():
        visits[k] = visits.get(k, 0) + v
    wa = _count_by(
        LeadWhatsAppMessage.objects.filter(
            client__company=p.company, direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND, **in_range
        ).filter(attr),
        "created_by_id",
    )
    sms = _count_by(
        LeadSMSMessage.objects.filter(
            client__company=p.company, direction=LeadSMSMessage.DIRECTION_OUTBOUND, **in_range
        ).filter(attr),
        "created_by_id",
    )
    status_changes = _count_by(
        transitions_in(p).exclude(from_status__isnull=True, from_status_name=""), "changed_by_id"
    )

    deals_created = _count_by(
        Deal.objects.filter(company=p.company, **in_range).filter(attr), "employee_id"
    )
    won = {
        r["employee_id"]: r
        for r in Deal.objects.filter(company=p.company, won_at__gte=p.start, won_at__lt=p.end)
        .filter(won_deals_q())
        .filter(attr)
        .values("employee_id")
        .annotate(n=Count("id"), revenue=Sum("value"))
    }

    local_start, local_end = p.start.astimezone(p.tz).date(), (p.end.astimezone(p.tz)).date()
    worked = {
        r["user_id"]: r["s"]
        for r in WorkDaySummary.objects.filter(company=p.company, work_date__gte=local_start, work_date__lte=local_end)
        .values("user_id")
        .annotate(s=Sum("active_seconds"))
    }

    rows = []
    for u in users:
        ps = periods.get(u.id, [])
        responded = [r for r in ps if r["first_response_at"] is not None]
        calls = call_counts.get(u.id, {"total": 0, "answered": 0, "missed": 0, "unknown": 0})
        w = won.get(u.id)
        rows.append(
            {
                "user_id": u.id,
                "name": u.get_full_name() or u.username,
                "role": u.role,
                "leads_assigned": len(ps),
                "leads_touched": len(responded),
                "touch_rate": round(100 * len(responded) / len(ps), 1) if ps else None,
                "response_wall": stats(r["response_seconds"] for r in responded),
                "response_business": stats(r["business_response_seconds"] for r in responded),
                "calls": calls,
                "tasks": tasks.get(u.id, 0),
                "visits": visits.get(u.id, 0),
                "whatsapp_sent": wa.get(u.id, 0),
                "sms_sent": sms.get(u.id, 0),
                "status_changes": status_changes.get(u.id, 0),
                "deals_created": deals_created.get(u.id, 0),
                "deals_won": w["n"] if w else 0,
                "revenue": float(w["revenue"] or 0) if w else 0.0,
                "worked_seconds": worked.get(u.id, 0) or 0,
            }
        )
    rows.sort(key=lambda r: (-r["deals_won"], -r["leads_touched"]))
    return {
        "rows": rows,
        "totals": {
            "leads_assigned": sum(r["leads_assigned"] for r in rows),
            "leads_touched": sum(r["leads_touched"] for r in rows),
            "calls": sum(r["calls"]["total"] for r in rows),
            "deals_won": sum(r["deals_won"] for r in rows),
            "revenue": sum(r["revenue"] for r in rows),
            "worked_seconds": sum(r["worked_seconds"] for r in rows),
        },
        "work_hours_tracking_enabled": bool(p.company.work_hours_tracking_enabled),
    }
