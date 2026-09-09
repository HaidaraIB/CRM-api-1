"""
Omni-Channel Inbox access control.

Deliberately the mirror image of whatsapp_access.py on one axis: CALL_CENTER is
the *primary* audience here — triaging DMs and converting the good ones into
leads is the role's job — whereas WhatsApp denies it outright
(whatsapp_access.py:36-37, 49-50). That WhatsApp rule stays untouched; the two
coexist because the inbox endpoints are their own function views and never mount
DenyCallCenterNonLeadAPI.

Employee/Doctor are scoped: they see a conversation only once it has been
converted into a lead assigned to them. Before conversion there is no lead and
therefore no ownership to derive, which is exactly why triage belongs to the
call-center pool.
"""

from __future__ import annotations

from typing import Optional

from django.db.models import QuerySet


def _is_authenticated(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def user_can_access_social_inbox(user) -> bool:
    """Whether this user may open the Inbox at all."""
    if not _is_authenticated(user):
        return False
    if user.is_admin() or user.is_call_center():
        return True
    if user.is_supervisor():
        return user.supervisor_has_permission("manage_social_inbox")
    # Reception and data entry have no triage role here.
    if user.is_reception() or user.is_data_entry():
        return False
    # Employee/Doctor: allowed, but scoped to their own converted leads.
    return bool(getattr(user, "is_assigned_clinical_staff", lambda: False)())


def user_sees_all_social_conversations(user) -> bool:
    """Company-wide visibility across every conversation, converted or not."""
    if not _is_authenticated(user):
        return False
    if user.is_admin() or user.is_call_center():
        return True
    if user.is_supervisor():
        return user.supervisor_has_permission("manage_social_inbox")
    return False


def user_is_social_staff_scoped(user) -> bool:
    """Employee/Doctor: limited to conversations tied to their own leads."""
    return bool(user and getattr(user, "is_assigned_clinical_staff", lambda: False)())


def filter_social_conversations_queryset(user, queryset: QuerySet) -> QuerySet:
    """Scope a SocialConversation queryset for this viewer."""
    if not _is_authenticated(user):
        return queryset.none()
    company = getattr(user, "company", None)
    if not company:
        return queryset.none()

    queryset = queryset.filter(company=company)
    if user_sees_all_social_conversations(user):
        return queryset
    if user_is_social_staff_scoped(user):
        # Converted lead assigned to this employee/doctor — not conversation.assigned_to.
        # That field is set at convert time and can go stale after a lead reassignment;
        # it also must not leak an unconverted DM just because someone tagged an agent.
        return queryset.filter(client__assigned_to_id=user.id)
    return queryset.none()


def user_can_access_social_conversation(user, conversation) -> bool:
    if not _is_authenticated(user) or conversation is None:
        return False
    company = getattr(user, "company", None)
    if not company or getattr(conversation, "company_id", None) != company.id:
        return False
    if user_sees_all_social_conversations(user):
        return True
    if user_is_social_staff_scoped(user):
        client = getattr(conversation, "client", None)
        return bool(client and client.assigned_to_id == user.id)
    return False


def require_social_conversation_access(user, conversation) -> Optional[str]:
    """
    Returns an error_key when denied, else None.

    Callers answer 404 with this key rather than 403 so a scoped employee cannot
    probe which conversations exist under other assignees.
    """
    if user_can_access_social_conversation(user, conversation):
        return None
    return "social_conversation_not_found"


def user_can_convert_social_conversation(user) -> bool:
    """Who may turn a conversation into a CRM lead and pick its assignee."""
    if not _is_authenticated(user):
        return False
    if user.is_admin() or user.is_call_center():
        return True
    if user.is_supervisor():
        return user.supervisor_has_permission(
            "manage_social_inbox"
        ) or user.supervisor_has_permission("manage_leads")
    return False


def user_can_delete_social_history(user) -> bool:
    """Owner/admin only: message history is a record, staff may not erase it."""
    return bool(_is_authenticated(user) and user.is_admin())
