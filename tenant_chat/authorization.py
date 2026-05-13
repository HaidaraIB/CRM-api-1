"""
Role-based DM eligibility within one company (symmetric).

See tenant_chat.policy for locked decisions. Chat authorization is independent of
supervisor CRM permission flags (e.g. can_manage_users).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounts.models import User


def _tenant_may_use_chat(user: "User") -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_super_admin():
        return False
    if getattr(user, "is_superuser", False) and not user.company_id:
        return False
    return bool(user.company_id)


def supervisor_chat_is_active(user: "User") -> bool:
    """
    Supervisors count as chat-eligible only when SupervisorPermission exists and is_active.
    Missing permission row => not eligible (same as inactive for participant lists).
    """
    if not user.is_supervisor():
        return False
    try:
        sp = user.supervisor_permissions
    except Exception:
        return False
    return bool(sp.is_active)


def chat_role_bucket(user: "User") -> str:
    """
    Returns one of: owner, supervisor, employee_lane, ineligible.
    employee_lane = employee OR data_entry (identical rules).
    """
    if not _tenant_may_use_chat(user):
        return "ineligible"
    if user.is_admin():
        return "owner"
    if user.is_supervisor():
        if not supervisor_chat_is_active(user):
            return "ineligible"
        return "supervisor"
    if user.is_employee() or user.is_data_entry():
        return "employee_lane"
    return "ineligible"


def can_chat(user_a: "User", user_b: "User") -> bool:
    """
    True if both users may participate in a direct tenant chat (same company,
    symmetric). Employee/Data Entry peers cannot chat with each other.
    """
    if user_a.pk == user_b.pk:
        return False
    if user_a.company_id != user_b.company_id or not user_a.company_id:
        return False

    ba = chat_role_bucket(user_a)
    bb = chat_role_bucket(user_b)
    if ba == "ineligible" or bb == "ineligible":
        return False

    if ba == "employee_lane" and bb == "employee_lane":
        return False

    return True


def eligible_company_users_queryset(base_qs):
    """
    Filter a User queryset to users who may appear as DM targets for listing.
    Excludes ineligible roles and inactive supervisors per policy.
    """
    from django.db.models import Q

    # Anyone with bucket != ineligible can appear; easiest: exclude users who fail bucket
    # We replicate filters used in chat_role_bucket for SQL efficiency where possible.
    return (
        base_qs.filter(company_id__isnull=False)
        .exclude(role="super_admin")
        .filter(
            Q(role="admin")
            | (
                Q(role="supervisor")
                & Q(supervisor_permissions__is_active=True)
            )
            | Q(role="employee")
            | Q(role="data_entry")
        )
        .distinct()
    )


def user_participates_in_conversation(user: "User", conversation) -> bool:
    from .models import ChatConversation

    if not user or not getattr(user, "id", None) or not getattr(conversation, "company_id", None):
        return False
    if user.company_id != conversation.company_id:
        return False
    if getattr(conversation, "kind", None) == ChatConversation.Kind.COMPANY_GROUP:
        return chat_role_bucket(user) != "ineligible"
    return user.id in (
        conversation.participant_low_id,
        conversation.participant_high_id,
    )


def user_can_access_chat_message(user: "User", message) -> bool:
    conv = getattr(message, "conversation", None)
    if not conv:
        return False
    return user_participates_in_conversation(user, conv)
