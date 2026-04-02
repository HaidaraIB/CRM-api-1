"""
Business logic for the CRM app, separated from HTTP/view concerns.
"""
from django.utils import timezone
from .models import Client, ClientEvent


def assign_unassigned_clients(company, employee, triggered_by):
    """
    Assign all unassigned clients of *company* to *employee*.
    Returns (assigned_count, employee_display_name).
    """
    unassigned = list(Client.objects.filter(company=company, assigned_to__isnull=True))
    if not unassigned:
        return 0, None

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
    return len(unassigned), name


def bulk_assign_clients(client_ids, company, target_user, triggered_by):
    """
    Assign a list of clients (by ID) to *target_user* (or unassign if None).
    Returns the number of actually changed clients.
    """
    clients = list(Client.objects.filter(id__in=client_ids, company=company))
    now = timezone.now()
    new_name = (
        (target_user.get_full_name() or target_user.username)
        if target_user
        else "Unassigned"
    )

    changed = []
    events = []

    for client in clients:
        if client.assigned_to == target_user:
            continue
        old_name = (
            client.assigned_to.get_full_name() or client.assigned_to.username
        ) if client.assigned_to else "Unassigned"

        client.assigned_to = target_user
        client.assigned_at = now if target_user else None
        changed.append(client)

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

    return len(changed)
