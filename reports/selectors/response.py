from collections import Counter, defaultdict

from django.utils import timezone

from reports.business_time import business_seconds_between
from reports.models import ConversationReply

from .base import BUCKET_KEYS, bucket_for, client_attr_q, periods_in, sla_seconds, stats

PENDING_LIMIT = 10


def _display(first, last, username):
    return f"{first or ''} {last or ''}".strip() or username


def _employee_row(user_id, name, rows, sla, now):
    responded = [r for r in rows if r["first_response_at"] is not None]
    raw = [r["response_seconds"] for r in responded]
    biz = [r["business_response_seconds"] for r in responded]
    pending = sum(1 for r in rows if r["first_response_at"] is None and r["ended_at"] is None)
    missed = sum(1 for r in rows if r["first_response_at"] is None and r["ended_at"] is not None)
    within = sum(
        1 for r in responded if (r["business_response_seconds"] if r["business_response_seconds"] is not None
                                 else r["response_seconds"]) <= sla
    )
    return {
        "user_id": user_id,
        "name": name,
        "assignments": len(rows),
        "responded": len(responded),
        "pending": pending,
        "missed": missed,
        "wall": stats(raw),
        "business": stats(biz),
        "within_sla": within,
        "within_sla_pct": round(100 * within / len(responded), 1) if responded else None,
    }


def _reply_stats(replies):
    return {"wall": stats(r.wall_seconds for r in replies), "business": stats(r.business_seconds for r in replies)}


def build(p) -> dict:
    now = timezone.now()
    sla = sla_seconds(p)
    qs = periods_in(p)
    rows = list(
        qs.values(
            "user_id", "user__first_name", "user__last_name", "user__username", "assigned_at", "ended_at",
            "first_response_at", "first_response_kind", "response_seconds", "business_response_seconds",
        )
    )

    by_user = defaultdict(list)
    names = {}
    for r in rows:
        by_user[r["user_id"]].append(r)
        names[r["user_id"]] = _display(r["user__first_name"], r["user__last_name"], r["user__username"])
    employees = sorted(
        (_employee_row(uid, names[uid], rs, sla, now) for uid, rs in by_user.items()),
        key=lambda e: (e["business"]["median"] is None, e["business"]["median"] or 0),
    )

    responded = [r for r in rows if r["first_response_at"] is not None]
    buckets = {
        "business": Counter(bucket_for(r["business_response_seconds"]) for r in rows),
        "wall": Counter(bucket_for(r["response_seconds"]) for r in rows),
    }
    distribution = {
        basis: [{"bucket": k, "count": c[k]} for k in BUCKET_KEYS] for basis, c in buckets.items()
    }

    grid = [[{"count": 0, "_sum": 0, "_n": 0} for _ in range(24)] for _ in range(7)]
    for r in rows:
        local = r["assigned_at"].astimezone(p.tz)
        cell = grid[local.weekday()][local.hour]
        cell["count"] += 1
        if r["business_response_seconds"] is not None:
            cell["_sum"] += r["business_response_seconds"]
            cell["_n"] += 1
    heatmap = [
        [{"count": c["count"], "avg_business": round(c["_sum"] / c["_n"]) if c["_n"] else None} for c in day]
        for day in grid
    ]

    pending_rows = (
        qs.filter(first_response_at__isnull=True, ended_at__isnull=True)
        .select_related("user", "user__company", "client")
        .order_by("assigned_at")[:PENDING_LIMIT]
    )
    oldest_pending = [
        {
            "client_id": a.client_id,
            "client": a.client.name,
            "user_id": a.user_id,
            "user": a.user.get_full_name() or a.user.username,
            "assigned_at": a.assigned_at.isoformat(),
            "waiting_seconds": int((now - a.assigned_at).total_seconds()),
            "waiting_business_seconds": business_seconds_between(a.assigned_at, now, a.user, p.company),
        }
        for a in pending_rows
    ]

    replies = list(
        ConversationReply.objects.filter(company=p.company, replied_at__gte=p.start, replied_at__lt=p.end)
        .filter(client_attr_q(p, "client__"))
        .filter(**({"replied_by_id__in": p.user_ids} if p.user_ids else {}))
        .select_related("replied_by")
    )
    replies_by_channel, replies_by_user = defaultdict(list), defaultdict(list)
    for rep in replies:
        replies_by_channel[rep.channel].append(rep)
        replies_by_user[rep.replied_by_id].append(rep)

    return {
        "sla_seconds": sla,
        "summary": {
            "assignments": len(rows),
            "responded": len(responded),
            "pending": sum(1 for r in rows if r["first_response_at"] is None and r["ended_at"] is None),
            "wall": stats(r["response_seconds"] for r in responded),
            "business": stats(r["business_response_seconds"] for r in responded),
        },
        "employees": employees,
        "distribution": distribution,
        "heatmap": heatmap,
        "by_kind": [{"kind": k or "unknown", "count": c} for k, c in
                    Counter(r["first_response_kind"] for r in responded).most_common()],
        "oldest_pending": oldest_pending,
        "conversation_replies": {
            "total": len(replies),
            "by_channel": [{"channel": ch, **_reply_stats(rs), "count": len(rs)} for ch, rs in replies_by_channel.items()],
            "by_employee": [
                {
                    "user_id": uid,
                    "name": (rs[0].replied_by.get_full_name() or rs[0].replied_by.username) if rs[0].replied_by else "—",
                    "count": len(rs),
                    **_reply_stats(rs),
                }
                for uid, rs in replies_by_user.items()
            ],
        },
    }
