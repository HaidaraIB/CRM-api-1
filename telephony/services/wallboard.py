"""
Live calls and queue wallboard (``GET /telephony/wallboard/``).

Built from what nodes already report: live ``PbxCall`` rows, the queue wait from
``call.queued`` / ``call.dequeued`` (contract section 5), and agent presence. It is
a read the client repeats when the ``telephony`` sync slice moves (version-token
polling, plan: no data socket), so it stays a handful of indexed queries.

* Queue stats cover today in the platform time zone (``settings.TIME_ZONE``).
* Queue counts are company-wide; the live-call list is filtered like the call
  history (``visible_calls``), so an agent sees their own calls only.
"""

from __future__ import annotations

from datetime import datetime, time

from django.db.models import Q
from django.utils import timezone

from ..models import PbxCall, PbxQueue
from .agents import LIVE_CALL_WINDOW, busy_extension_ids, presence

# A caller answered within this many seconds counts toward the service level.
SERVICE_LEVEL_S = 20


def _today_start():
    now = timezone.localtime()
    return timezone.make_aware(datetime.combine(now.date(), time.min), now.tzinfo)


def _seconds(a, b) -> int:
    return max(0, int((b - a).total_seconds()))


def queue_stats(company_id) -> list[dict]:
    now = timezone.now()
    since = _today_start()
    queues = list(PbxQueue.objects.filter(company_id=company_id).prefetch_related("members__extension").order_by("name"))
    calls = list(
        PbxCall.objects.filter(company_id=company_id, queue__isnull=False)
        .filter(Q(queued_at__gte=since) | Q(queue_left_at__isnull=True, queued_at__gte=now - LIVE_CALL_WINDOW))
        .values("queue_id", "queued_at", "queue_left_at", "queue_outcome", "status")
    )
    busy = busy_extension_ids(company_id)
    out = []
    for queue in queues:
        mine = [c for c in calls if c["queue_id"] == queue.id]
        waiting = [c for c in mine if c["queue_left_at"] is None and c["status"] in PbxCall.LIVE_STATUSES]
        today = [c for c in mine if c["queued_at"] >= since and c["queue_left_at"] is not None]
        answered = [c for c in today if c["queue_outcome"] == "answered"]
        waits = [_seconds(c["queued_at"], c["queue_left_at"]) for c in answered]
        members = [m.extension for m in queue.members.all() if m.extension.status == "active"]
        out.append({
            "id": str(queue.id),
            "name": queue.name,
            "waiting": len(waiting),
            "longest_wait_s": max((_seconds(c["queued_at"], now) for c in waiting), default=0),
            "agents": {
                "members": len(members),
                "available": sum(1 for e in members if e.registered and e.agent_status == "available" and e.id not in busy),
                "on_call": sum(1 for e in members if e.id in busy),
                "paused": sum(1 for e in members if e.agent_status == "paused"),
                "offline": sum(1 for e in members if not e.registered),
            },
            "today": {
                "offered": len(today),
                "answered": len(answered),
                "abandoned": sum(1 for c in today if c["queue_outcome"] == "abandoned"),
                "overflow": sum(1 for c in today if c["queue_outcome"] == "overflow"),
                "avg_wait_s": round(sum(waits) / len(waits)) if waits else None,
                "max_wait_s": max(waits, default=None),
                "service_level_pct": (
                    round(100 * sum(1 for w in waits if w <= SERVICE_LEVEL_S) / len(today)) if today else None
                ),
            },
        })
    return out


def live_calls(calls_qs) -> list[dict]:
    """Calls still in progress (newest first), from an already visibility-filtered queryset."""
    rows = (
        calls_qs.filter(status__in=PbxCall.LIVE_STATUSES, started_at__gte=timezone.now() - LIVE_CALL_WINDOW)
        .select_related("extension", "answered_by", "crm_contact", "queue")
        .order_by("-started_at")[:100]
    )
    return [{
        "id": str(c.id),
        "direction": c.direction,
        "status": c.status,
        "from_party": c.from_party,
        "to_party": c.to_party,
        "did": c.did,
        "extension_number": c.extension.number if c.extension else None,
        "answered_by_number": c.answered_by.number if c.answered_by else None,
        "crm_contact": c.crm_contact_id,
        "crm_contact_name": c.crm_contact.name if c.crm_contact else None,
        "queue": str(c.queue_id) if c.queue_id else None,
        "queue_name": c.queue.name if c.queue else None,
        "waiting": bool(c.queued_at and c.queue_left_at is None),
        "on_hold": c.on_hold,
        "started_at": c.started_at,
        "answered_at": c.answered_at,
        "queued_at": c.queued_at,
    } for c in rows]


def wallboard(user, calls_qs) -> dict:
    company_id = user.company_id
    since = _today_start()
    today = PbxCall.objects.filter(company_id=company_id, started_at__gte=since)
    return {
        "generated_at": timezone.now(),
        "since": since,
        "service_level_s": SERVICE_LEVEL_S,
        "queues": queue_stats(company_id),
        "live_calls": live_calls(calls_qs),
        "agents": presence(company_id),
        "today": {
            "calls": today.count(),
            "answered": today.filter(status=PbxCall.Status.ANSWERED).count(),
            "missed": today.filter(
                direction=PbxCall.Direction.INBOUND,
                status__in=[PbxCall.Status.MISSED, PbxCall.Status.ABANDONED, PbxCall.Status.NO_ANSWER],
            ).count(),
        },
    }


__all__ = ["wallboard", "queue_stats", "live_calls", "SERVICE_LEVEL_S"]
