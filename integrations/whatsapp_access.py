"""
WhatsApp chat access control.

Staff (Employee/Doctor) may only open threads for leads assigned to them.
Owners/admins and other company-wide lead viewers (reception, data entry,
supervisors with manage_leads) see all company WhatsApp threads.

Manual phone lookup for staff: if the number belongs to another assignee
(or is unassigned / unknown), respond as not found — do not leak ownership.
"""

from __future__ import annotations

from typing import Optional

from django.db.models import QuerySet


def user_sees_all_company_leads(user) -> bool:
    """True when the user may see every lead in their company (CRM list scope)."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_admin() or user.is_reception() or user.is_data_entry():
        return True
    if user.is_supervisor() and user.supervisor_has_permission("manage_leads"):
        return True
    return False


def user_can_access_whatsapp_chats(user) -> bool:
    """Per-user toggle: owner may disable WhatsApp chat access for an employee/supervisor."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_admin():
        return True
    if user.is_call_center():
        return False
    if user.is_supervisor():
        return user.supervisor_has_permission("manage_whatsapp_chats")
    return bool(getattr(user, "whatsapp_chat_enabled", True))


def user_can_access_whatsapp_calls(user) -> bool:
    """Per-user toggle: owner may disable WhatsApp calling access for an employee/supervisor."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_admin():
        return True
    if user.is_call_center():
        return False
    if user.is_supervisor():
        return user.supervisor_has_permission("manage_whatsapp_calls")
    return bool(getattr(user, "whatsapp_call_enabled", True))


def user_is_whatsapp_staff_scoped(user) -> bool:
    """Employee/Doctor: WhatsApp threads limited to assigned leads."""
    return bool(user and getattr(user, "is_assigned_clinical_staff", lambda: False)())


def user_can_access_client(user, client) -> bool:
    """Whether this user may view/send WhatsApp for the given CRM client."""
    if not user or not client:
        return False
    company = getattr(user, "company", None)
    if not company or getattr(client, "company_id", None) != company.id:
        return False
    if user_sees_all_company_leads(user):
        return True
    if user_is_whatsapp_staff_scoped(user):
        return getattr(client, "assigned_to_id", None) == user.id
    return False


def filter_clients_queryset_for_whatsapp(user, queryset: QuerySet) -> QuerySet:
    """Scope a Client queryset the same way as WhatsApp conversation lists."""
    if user_sees_all_company_leads(user):
        return queryset
    if user_is_whatsapp_staff_scoped(user):
        return queryset.filter(assigned_to_id=user.id)
    return queryset.none()


def filter_whatsapp_messages_queryset(user, queryset: QuerySet) -> QuerySet:
    """Scope LeadWhatsAppMessage queryset by assignee for staff."""
    if user_sees_all_company_leads(user):
        return queryset
    if user_is_whatsapp_staff_scoped(user):
        return queryset.filter(client__assigned_to_id=user.id)
    return queryset.none()


def resolve_accessible_client_by_phone(user, phone: str):
    """
    Resolve a CRM client by phone for WhatsApp UI.

    Returns:
      (client, None) — accessible client
      (None, 'whatsapp_contact_not_found') — staff: missing / not assigned / someone else's
      (None, None) — company-wide viewer: no CRM lead (manual chat allowed)
    """
    from integrations.services.phone_match import find_client_by_phone

    company = getattr(user, "company", None)
    prefer = user if user_is_whatsapp_staff_scoped(user) else None
    client = find_client_by_phone(company, phone, prefer_assigned_to=prefer)

    if user_is_whatsapp_staff_scoped(user):
        if not client or not user_can_access_client(user, client):
            return None, "whatsapp_contact_not_found"
        return client, None

    if client and not user_can_access_client(user, client):
        return None, "whatsapp_contact_not_found"
    return client, None


def require_client_whatsapp_access(user, client) -> Optional[str]:
    """Return error_key if access denied, else None."""
    if user_can_access_client(user, client):
        return None
    return "whatsapp_contact_not_found"
