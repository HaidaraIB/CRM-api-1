"""
Business logic for the CRM app, separated from HTTP/view concerns.
"""
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from crm.availability import user_accepts_new_assignments
from .models import Client, ClientEvent


def assign_unassigned_clients(company, employee, triggered_by):
    """
    Assign all unassigned clients of *company* to *employee*.
    Returns (assigned_count, employee_display_name).
    """
    if employee and not user_accepts_new_assignments(employee):
        raise ValidationError(
            {
                "assigned_to": "Cannot assign to this user on their weekly day off.",
                "error_key": "employee_weekly_day_off",
            }
        )
    unassigned = list(Client.objects.filter(company=company, assigned_to__isnull=True))
    if not unassigned:
        return 0, None

    from crm.signals import notify_lead_assignment_change

    now = timezone.now()
    name = employee.get_full_name() or employee.username
    events = []

    for client in unassigned:
        client.assigned_to = employee
        client.assigned_at = now
        events.append(
            ClientEvent(
                client=client,
                event_type="assignment",
                old_value="Unassigned",
                new_value=name,
                notes=f"Auto-assigned to {name}",
                created_by=triggered_by,
            )
        )

    Client.objects.bulk_update(unassigned, ["assigned_to", "assigned_at"])
    ClientEvent.objects.bulk_create(events)
    for client in unassigned:
        notify_lead_assignment_change(
            client=client,
            old_assignee=None,
            new_assignee=employee,
            actor=triggered_by,
        )
    return len(unassigned), name


def bulk_assign_clients(client_ids, company, target_user, triggered_by):
    """
    Assign a list of clients (by ID) to *target_user* (or unassign if None).
    Returns the number of actually changed clients.
    """
    if target_user and not user_accepts_new_assignments(target_user):
        raise ValidationError(
            {
                "user_id": "Cannot assign to this user on their weekly day off.",
                "error_key": "employee_weekly_day_off",
            }
        )
    clients = list(
        Client.objects.filter(id__in=client_ids, company=company).select_related(
            "assigned_to"
        )
    )
    now = timezone.now()
    new_name = (
        (target_user.get_full_name() or target_user.username)
        if target_user
        else "Unassigned"
    )

    from crm.signals import notify_lead_assignment_change

    changed = []
    events = []
    notification_changes = []

    for client in clients:
        if client.assigned_to == target_user:
            continue
        old_assignee = client.assigned_to
        old_name = (
            old_assignee.get_full_name() or old_assignee.username
        ) if old_assignee else "Unassigned"

        client.assigned_to = target_user
        client.assigned_at = now if target_user else None
        changed.append(client)
        notification_changes.append((client, old_assignee))

        notes = (
            f"Bulk assigned to {new_name} (was {old_name})"
            if target_user
            else f"Unassigned (was {old_name})"
        )
        events.append(
            ClientEvent(
                client=client,
                event_type="assignment",
                old_value=old_name,
                new_value=new_name,
                notes=notes,
                created_by=triggered_by,
            )
        )

    if changed:
        Client.objects.bulk_update(changed, ["assigned_to", "assigned_at"])
        ClientEvent.objects.bulk_create(events)
        for client, old_assignee in notification_changes:
            notify_lead_assignment_change(
                client=client,
                old_assignee=old_assignee,
                new_assignee=target_user,
                actor=triggered_by,
            )

    return len(changed)


def distribute_clients_to_least_busy(company, clients, triggered_by, *, event_notes=None):
    """
    Assign each client via the company auto-assign algorithm (least-busy / RR).

    Bulk-safe: plans all picks in memory, then ``bulk_update`` + ``bulk_create``
    events. Skips per-lead save signals / FCM (those time out at hundreds of leads).
    Single-lead assignment paths are unchanged and still notify normally.
    """
    from crm.assignment import plan_bulk_auto_assignments

    clients = list(clients)
    if not clients:
        return {
            "assigned_count": 0,
            "skipped_count": 0,
            "assignments": [],
            "assignee_names": [],
        }

    now = timezone.now()
    assigned_count = 0
    skipped_count = 0
    assignments = []
    assignee_names = set()
    events = []
    changed = []

    picks = plan_bulk_auto_assignments(company, clients)

    for client, employee in zip(clients, picks):
        if not employee:
            skipped_count += 1
            continue

        old_name = (
            client.assigned_to.get_full_name() or client.assigned_to.username
        ) if client.assigned_to else "Unassigned"
        new_name = employee.get_full_name() or employee.username
        assignee_names.add(new_name)

        client.assigned_to = employee
        client.assigned_at = now
        changed.append(client)

        if event_notes is None:
            notes = f"Reassigned after employee deactivation to {new_name}"
        elif callable(event_notes):
            notes = event_notes(client, employee, old_name, new_name)
        else:
            notes = event_notes.format(assignee=new_name, old=old_name)

        events.append(
            ClientEvent(
                client=client,
                event_type="assignment",
                old_value=old_name,
                new_value=new_name,
                notes=notes,
                created_by=triggered_by,
            )
        )
        assignments.append(
            {"client_id": client.id, "assignee_id": employee.id, "assignee_name": new_name}
        )
        assigned_count += 1

    if changed:
        Client.objects.bulk_update(changed, ["assigned_to", "assigned_at"])
    if events:
        ClientEvent.objects.bulk_create(events)

    return {
        "assigned_count": assigned_count,
        "skipped_count": skipped_count,
        "assignments": assignments,
        "assignee_names": sorted(assignee_names),
    }
