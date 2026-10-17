"""
Best-effort reconstruction of report history from legacy ``ClientEvent`` rows and activity tables.

ClientEvent stores display names, so statuses/users are matched by name (case-insensitive, within
the company); anything that cannot be matched keeps its name snapshot with a null FK. Only history
*before* the live recorder took over a lead is rebuilt, and every row is flagged ``is_backfilled``
so reports can tell the user how much of a range is reconstructed. Safe to re-run.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from crm.models import Client, ClientCall, ClientEvent, ClientFieldVisit, ClientTask, ClientVisit, Deal
from integrations.models import LeadSMSMessage, LeadWhatsAppMessage
from settings.models import LeadStatus

from .business_time import business_seconds_between
from .models import ConversationReply, LeadAssignmentPeriod, LeadStatusTransition

K = LeadAssignmentPeriod


def _name_key(value) -> str:
    return (value or "").strip().lower()


def _user_name(user) -> str:
    return user.get_full_name() or user.username


def _first_activity(client, user, since, until):
    """Earliest (datetime, kind) of an action by ``user`` on ``client`` within [since, until)."""
    candidates = []

    def add(qs, field, kind):
        row = qs.aggregate(m=Min(field))["m"]
        if row is not None:
            candidates.append((row, kind))

    def window(qs, field):
        qs = qs.filter(**{f"{field}__gte": since})
        return qs.filter(**{f"{field}__lt": until}) if until else qs

    add(window(ClientCall.objects.filter(client=client, created_by=user), "created_at"), "created_at", K.KIND_CALL)
    add(window(ClientTask.objects.filter(client=client, created_by=user), "created_at"), "created_at", K.KIND_TASK)
    add(window(ClientVisit.objects.filter(client=client, created_by=user), "created_at"), "created_at", K.KIND_VISIT)
    add(
        window(ClientFieldVisit.objects.filter(client=client, created_by=user), "created_at"),
        "created_at",
        K.KIND_VISIT,
    )
    add(window(Deal.objects.filter(client=client, employee=user), "created_at"), "created_at", K.KIND_DEAL)
    add(
        window(
            LeadWhatsAppMessage.objects.filter(
                client=client, created_by=user, direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND
            ),
            "created_at",
        ),
        "created_at",
        K.KIND_WHATSAPP,
    )
    add(
        window(
            LeadSMSMessage.objects.filter(client=client, created_by=user, direction=LeadSMSMessage.DIRECTION_OUTBOUND),
            "created_at",
        ),
        "created_at",
        K.KIND_SMS,
    )
    add(
        window(ClientEvent.objects.filter(client=client, created_by=user, event_type="status_change"), "created_at"),
        "created_at",
        K.KIND_STATUS,
    )
    return min(candidates) if candidates else (None, "")


def _backfill_transitions(client, statuses_by_name, events):
    live_first = (
        LeadStatusTransition.objects.filter(client=client, is_backfilled=False).order_by("changed_at").first()
    )
    cutoff = live_first.changed_at if live_first else None
    has_live_initial = LeadStatusTransition.objects.filter(
        client=client, is_backfilled=False, from_status__isnull=True, from_status_name=""
    ).exists()

    changes = [e for e in events if e.event_type == "status_change" and (cutoff is None or e.created_at < cutoff)]
    rows = []
    if not has_live_initial:
        if changes:
            first_name = changes[0].old_value or ""
            initial = statuses_by_name.get(_name_key(first_name))
            initial_name = first_name
        else:
            initial, initial_name = (client.status, client.status.name) if client.status_id else (None, "")
        if initial is not None or initial_name:
            rows.append(
                LeadStatusTransition(
                    company_id=client.company_id,
                    client=client,
                    from_status=None,
                    to_status=initial,
                    to_status_name=initial_name or (initial.name if initial else ""),
                    changed_at=client.created_at,
                    source=LeadStatusTransition.SOURCE_BACKFILL,
                    is_backfilled=True,
                )
            )
    previous_at = client.created_at
    for ev in changes:
        frm = statuses_by_name.get(_name_key(ev.old_value))
        to = statuses_by_name.get(_name_key(ev.new_value))
        rows.append(
            LeadStatusTransition(
                company_id=client.company_id,
                client=client,
                from_status=frm,
                to_status=to,
                from_status_name=ev.old_value or "",
                to_status_name=ev.new_value or "",
                changed_by=ev.created_by,
                changed_at=ev.created_at,
                source=LeadStatusTransition.SOURCE_BACKFILL,
                reason=ev.reason or "",
                seconds_in_previous=max(0, int((ev.created_at - previous_at).total_seconds())),
                is_backfilled=True,
            )
        )
        previous_at = ev.created_at
    LeadStatusTransition.objects.bulk_create(rows)
    return len(rows)


def _backfill_assignments(client, users_by_name, events):
    live_first = LeadAssignmentPeriod.objects.filter(client=client, is_backfilled=False).order_by("assigned_at").first()
    cutoff = live_first.assigned_at if live_first else None

    timeline = []  # (time, user|None, assigned_by)
    for ev in events:
        if ev.event_type not in ("assignment", "re_assignment"):
            continue
        if cutoff is not None and ev.created_at >= cutoff:
            continue
        new = (ev.new_value or "").strip()
        user = None if _name_key(new) in ("", "unassigned") else users_by_name.get(_name_key(new))
        if user is None and _name_key(new) not in ("", "unassigned"):
            continue  # unmatched name (renamed/removed user): cannot attribute, skip
        timeline.append((ev.created_at, user, ev.created_by if ev.event_type == "assignment" else None))

    if not timeline and cutoff is None and client.assigned_to_id and client.assigned_at:
        timeline.append((client.assigned_at, client.assigned_to, None))
    elif timeline and cutoff is None and client.assigned_to_id and client.assigned_at:
        if timeline[-1][1] is None or timeline[-1][1].pk != client.assigned_to_id:
            timeline.append((client.assigned_at, client.assigned_to, None))

    periods = []
    for idx, (at, user, by) in enumerate(timeline):
        if user is None:
            continue
        end = timeline[idx + 1][0] if idx + 1 < len(timeline) else cutoff
        periods.append(
            LeadAssignmentPeriod(
                company_id=client.company_id,
                client=client,
                user=user,
                assigned_by=by,
                source="backfill",
                assigned_at=at,
                ended_at=end,
                is_backfilled=True,
            )
        )
    for p in periods:
        at, kind = _first_activity(client, p.user, p.assigned_at, p.ended_at)
        if at is not None:
            p.first_response_at, p.first_response_kind = at, kind
            p.response_seconds = max(0, int((at - p.assigned_at).total_seconds()))
            p.business_response_seconds = business_seconds_between(p.assigned_at, at, p.user, client.company)
    LeadAssignmentPeriod.objects.bulk_create(periods)
    return len(periods)


def _backfill_replies(client):
    created = 0
    for model, channel in (
        (LeadWhatsAppMessage, ConversationReply.CHANNEL_WHATSAPP),
        (LeadSMSMessage, ConversationReply.CHANNEL_SMS),
    ):
        pending_inbound = None
        out = []
        for msg in model.objects.filter(client=client).order_by("created_at", "pk"):
            if msg.direction == model.DIRECTION_INBOUND:
                if pending_inbound is None:
                    pending_inbound = msg.created_at
            elif pending_inbound is not None and msg.created_by_id and msg.created_at > pending_inbound:
                out.append(
                    ConversationReply(
                        company_id=client.company_id,
                        client=client,
                        channel=channel,
                        inbound_at=pending_inbound,
                        replied_at=msg.created_at,
                        replied_by=msg.created_by,
                        wall_seconds=int((msg.created_at - pending_inbound).total_seconds()),
                        business_seconds=business_seconds_between(
                            pending_inbound, msg.created_at, msg.created_by, client.company
                        ),
                        is_backfilled=True,
                    )
                )
                pending_inbound = None
        ConversationReply.objects.bulk_create(out)
        created += len(out)
    return created


def rebuild_company(company, since=None, log=None) -> dict:
    """Idempotently rebuild backfilled history for one company."""
    from accounts.models import User

    statuses_by_name = {_name_key(s.name): s for s in LeadStatus.objects.filter(company=company)}
    users_by_name = {}
    for u in User.objects.filter(company=company):
        users_by_name.setdefault(_name_key(_user_name(u)), u)
        users_by_name.setdefault(_name_key(u.username), u)

    leads = Client.objects.filter(company=company).select_related("status", "assigned_to", "company")
    if since:
        leads = leads.filter(created_at__gte=since)

    with transaction.atomic():
        for model in (LeadStatusTransition, LeadAssignmentPeriod, ConversationReply):
            qs = model.objects.filter(company=company, is_backfilled=True)
            if since:
                qs = qs.filter(client__created_at__gte=since)
            qs.delete()

    totals = {"leads": 0, "transitions": 0, "periods": 0, "replies": 0}
    for client in leads.iterator(chunk_size=500):
        with transaction.atomic():
            events = list(ClientEvent.objects.filter(client=client).select_related("created_by").order_by("created_at", "pk"))
            totals["transitions"] += _backfill_transitions(client, statuses_by_name, events)
            totals["periods"] += _backfill_assignments(client, users_by_name, events)
            totals["replies"] += _backfill_replies(client)
        totals["leads"] += 1
        if log and totals["leads"] % 500 == 0:
            log(f"  {company.name}: {totals['leads']} leads processed")
    return totals
