from collections import Counter, defaultdict

from django.db.models import Count, Q

from reports.models import LeadAssignmentPeriod, LeadStatusTransition

from .base import stats, transitions_in

MAX_PATH_ROWS = 50000


def _label(name: str) -> str:
    return name or "—"


def build(p) -> dict:
    qs = transitions_in(p)
    moved = qs.exclude(from_status__isnull=True, from_status_name="")

    matrix_rows = (
        moved.values("from_status_id", "from_status_name", "to_status_id", "to_status_name")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    matrix = [
        {
            "from": _label(r["from_status_name"]),
            "from_status_id": r["from_status_id"],
            "to": _label(r["to_status_name"]),
            "to_status_id": r["to_status_id"],
            "count": r["n"],
        }
        for r in matrix_rows
    ]
    names = sorted({m["from"] for m in matrix} | {m["to"] for m in matrix})
    sankey = {
        "nodes": [{"name": n} for n in names],
        "links": [
            {"source": names.index(m["from"]), "target": names.index(m["to"]), "value": m["count"]}
            for m in matrix
            if m["from"] != m["to"]
        ],
    }

    entered = [
        {
            "status_id": r["to_status_id"],
            "status": _label(r["to_status_name"]),
            "entries": r["n"],
            "leads": r["leads"],
        }
        for r in qs.values("to_status_id", "to_status_name")
        .annotate(n=Count("id"), leads=Count("client", distinct=True))
        .order_by("-n")
    ]

    durations = defaultdict(list)
    for name, secs in moved.exclude(seconds_in_previous__isnull=True).values_list("from_status_name", "seconds_in_previous"):
        durations[name].append(secs)
    time_in_status = sorted(
        ({"status": _label(n), **stats(v)} for n, v in durations.items()), key=lambda r: -(r["count"] or 0)
    )

    paths = defaultdict(list)
    for client_id, name in qs.order_by("client_id", "changed_at", "id").values_list("client_id", "to_status_name")[:MAX_PATH_ROWS]:
        if not paths[client_id] or paths[client_id][-1] != name:
            paths[client_id].append(name)
    common = Counter(" → ".join(_label(n) for n in path) for path in paths.values() if len(path) > 1)

    reopened = moved.filter(from_status__category="closed").exclude(to_status__category="closed").count()
    return {
        "transitions": moved.count(),
        "leads_with_changes": moved.values("client").distinct().count(),
        "matrix": matrix,
        "sankey": sankey,
        "entered": entered,
        "time_in_status": time_in_status,
        "common_paths": [{"path": k, "leads": v} for k, v in common.most_common(10)],
        "reopened": reopened,
        "by_source": [
            {"source": r["source"], "count": r["n"]}
            for r in moved.values("source").annotate(n=Count("id")).order_by("-n")
        ],
    }


def lead_journey(company, client) -> dict:
    """One lead's complete path and handling history, ordered, with the time spent in each stage."""
    from django.utils import timezone

    transitions = list(
        LeadStatusTransition.objects.filter(company=company, client=client)
        .select_related("changed_by")
        .order_by("changed_at", "id")
    )
    now = timezone.now()
    steps = []
    for i, t in enumerate(transitions):
        nxt = transitions[i + 1].changed_at if i + 1 < len(transitions) else None
        steps.append(
            {
                "status": t.to_status_name,
                "entered_at": t.changed_at.isoformat(),
                "left_at": nxt.isoformat() if nxt else None,
                "seconds_in_status": int(((nxt or now) - t.changed_at).total_seconds()),
                "changed_by": (t.changed_by.get_full_name() or t.changed_by.username) if t.changed_by else None,
                "source": t.source,
                "reason": t.reason,
                "is_backfilled": t.is_backfilled,
            }
        )
    periods = [
        {
            "user_id": a.user_id,
            "user": a.user.get_full_name() or a.user.username,
            "assigned_at": a.assigned_at.isoformat(),
            "ended_at": a.ended_at.isoformat() if a.ended_at else None,
            "first_response_at": a.first_response_at.isoformat() if a.first_response_at else None,
            "first_response_kind": a.first_response_kind,
            "response_seconds": a.response_seconds,
            "business_response_seconds": a.business_response_seconds,
            "is_backfilled": a.is_backfilled,
        }
        for a in LeadAssignmentPeriod.objects.filter(company=company, client=client).select_related("user").order_by("assigned_at")
    ]
    return {
        "lead": {"id": client.id, "name": client.name, "created_at": client.created_at.isoformat()},
        "status_path": steps,
        "assignments": periods,
        "total_seconds": int((now - client.created_at).total_seconds()),
    }
