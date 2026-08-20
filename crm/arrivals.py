"""
Walk-in arrival announcements (CALL_CENTER role): "customer arrived" routing, cooldown,
and acknowledgement. See crm/assignment.py for the auto-assign picker this reuses and
crm/availability.py for the on-shift-or-unscheduled predicate that drives routing.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from crm.assignment import get_arrival_assignee
from crm.availability import user_is_on_shift_or_unscheduled
from crm.models import Client, ClientEvent, LeadArrival, LeadArrivalRouting
from notifications.models import NotificationType
from notifications.services import NotificationService

ARRIVAL_COOLDOWN_SECONDS = 300


def arrival_escalation_recipients(company, *, exclude_user_ids=None):
    """
    Company owner + active supervisors with can_manage_leads, deduped, capped at 10.
    Shared by the off-shift-assignee announce-time notify and the escalation cron job.
    """
    exclude_user_ids = set(exclude_user_ids or [])
    recipients = []
    seen_ids = set()

    owner = getattr(company, "owner", None)
    if owner is not None and owner.is_active and owner.id not in exclude_user_ids:
        recipients.append(owner)
        seen_ids.add(owner.id)

    supervisors = User.objects.filter(
        company=company,
        role="supervisor",
        is_active=True,
        supervisor_permissions__is_active=True,
        supervisor_permissions__can_manage_leads=True,
    ).exclude(id__in=exclude_user_ids | seen_ids)

    for supervisor in supervisors:
        if supervisor.id in seen_ids:
            continue
        recipients.append(supervisor)
        seen_ids.add(supervisor.id)
        if len(recipients) >= 10:
            break

    return recipients


def _recent_arrival_within_cooldown(client):
    """Must be called while holding a row lock on `client` to avoid a double-tap race."""
    cutoff = timezone.now() - timedelta(seconds=ARRIVAL_COOLDOWN_SECONDS)
    return (
        LeadArrival.objects.filter(client=client, announced_at__gte=cutoff)
        .order_by("-announced_at")
        .first()
    )


def route_arrival(client, company):
    """
    Decide who gets notified for this arrival and whether the lead gets (re)assigned.

    Returns (routing: str, notified_users: list[User], new_assignee: User | None).
    `new_assignee` is only set for the auto_assigned routing; the caller is responsible
    for actually writing it onto the client.
    """
    assignee = client.assigned_to
    if assignee is not None:
        if user_is_on_shift_or_unscheduled(assignee, company_for_calendar=company):
            return LeadArrivalRouting.EXISTING_ASSIGNEE.value, [assignee], None
        recipients = arrival_escalation_recipients(company)
        if not recipients:
            return LeadArrivalRouting.UNROUTABLE.value, [], None
        return LeadArrivalRouting.OWNER_ASSIGNEE_OFF_SHIFT.value, recipients, None

    picked = get_arrival_assignee(company)
    if picked is not None:
        return LeadArrivalRouting.AUTO_ASSIGNED.value, [picked], picked

    owner = getattr(company, "owner", None)
    if owner is not None and owner.is_active:
        return LeadArrivalRouting.OWNER_NO_ELIGIBLE.value, [owner], None
    return LeadArrivalRouting.UNROUTABLE.value, [], None


class ArrivalCooldownActive(Exception):
    """Raised when the same lead was announced within the cooldown window."""

    def __init__(self, existing_arrival):
        self.existing_arrival = existing_arrival
        super().__init__("Arrival cooldown active")


def announce_arrival(*, client_id, company, actor, notes=""):
    """
    Announce a walk-in arrival for `client_id`. Locks the client row for the cooldown
    check and any assignment write, so concurrent double-taps serialize instead of racing.

    Raises Client.DoesNotExist if the lead isn't in this company, or
    ArrivalCooldownActive if the same lead was announced within the last
    ARRIVAL_COOLDOWN_SECONDS (the existing arrival is attached to the exception).
    """
    with transaction.atomic():
        client = Client.objects.select_for_update().get(pk=client_id, company=company)

        existing = _recent_arrival_within_cooldown(client)
        if existing is not None:
            raise ArrivalCooldownActive(existing)

        routing, notified_users, new_assignee = route_arrival(client, company)
        assignee_at_arrival = client.assigned_to

        if new_assignee is not None:
            client.assigned_to = new_assignee
            client.assigned_at = timezone.now()
            client._skip_assignee_availability_check = True
            client.save(update_fields=["assigned_to", "assigned_at"])

        escalation_due_at = None
        if getattr(company, "arrival_escalation_enabled", True):
            minutes = getattr(company, "arrival_escalation_minutes", 5) or 5
            escalation_due_at = timezone.now() + timedelta(minutes=minutes)

        arrival = LeadArrival.objects.create(
            company=company,
            client=client,
            announced_by=actor,
            notes=notes or "",
            routing=routing,
            assignee_at_arrival=assignee_at_arrival,
            escalation_due_at=escalation_due_at,
        )
        if notified_users:
            arrival.notified_users.set(notified_users)

        notified_names = ", ".join(
            u.get_full_name() or u.username for u in notified_users
        ) or "—"
        ClientEvent.objects.create(
            client=client,
            event_type="customer_arrived",
            new_value=notified_names,
            notes=notes or "",
            created_by=actor,
        )

        transaction.on_commit(lambda: _send_arrival_notifications(arrival, notified_users))

    return arrival


def _send_arrival_notifications(arrival, notified_users):
    client = arrival.client
    for user in notified_users:
        notif_type = (
            NotificationType.CUSTOMER_ARRIVAL_ASSIGNEE_OFF_SHIFT
            if arrival.routing == LeadArrivalRouting.OWNER_ASSIGNEE_OFF_SHIFT.value
            else NotificationType.CUSTOMER_ARRIVED
        )
        NotificationService.send_notification(
            user=user,
            notification_type=notif_type,
            data={
                "kind": "lead_arrival",
                "arrival_id": arrival.id,
                "lead_id": client.id,
                "lead_name": client.name,
                "client_id": str(client.id),
                "routing": arrival.routing,
                "invalidate": "crm:arrivals",
            },
            skip_settings_check=True,
        )


def acknowledge_arrival(*, arrival, actor):
    """
    Idempotent: if already acknowledged, returns the arrival unchanged (no error, no
    duplicate notification) — the mobile action button and a retried request may both land.
    """
    with transaction.atomic():
        locked = LeadArrival.objects.select_for_update().get(pk=arrival.pk)
        if locked.acknowledged_at is not None:
            return locked

        locked.acknowledged_at = timezone.now()
        locked.acknowledged_by = actor
        locked.save(update_fields=["acknowledged_at", "acknowledged_by", "updated_at"])

        ClientEvent.objects.create(
            client=locked.client,
            event_type="customer_arrival_acknowledged",
            new_value=actor.get_full_name() or actor.username,
            created_by=actor,
        )

        announced_by = locked.announced_by
        if announced_by is not None and announced_by.id != actor.id:
            lead_name = locked.client.name
            transaction.on_commit(
                lambda: NotificationService.send_notification(
                    user=announced_by,
                    notification_type=NotificationType.CUSTOMER_ARRIVAL_ACKNOWLEDGED,
                    data={
                        "kind": "lead_arrival_ack",
                        "arrival_id": locked.id,
                        "lead_id": locked.client_id,
                        "lead_name": lead_name,
                        "client_id": str(locked.client_id),
                        "invalidate": "crm:arrivals",
                    },
                )
            )

    return locked
