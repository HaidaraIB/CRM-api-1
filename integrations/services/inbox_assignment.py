"""
Round-robin inbox chats and calls across ready call-center agents.

Ready means: call_center role, active, online, on shift (or no shift set),
not on leave, and not Away.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from accounts.models import Role
from accounts.presence import is_online
from crm.assignment import _pick_round_robin_among_tied
from crm.availability import user_is_on_shift_or_unscheduled
from integrations.models import (
    SocialConversation,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallStatus,
    WhatsAppConversationStatus,
    WhatsAppPurpose,
)
from integrations.services.whatsapp_call_availability import user_is_whatsapp_call_away

RING_ASSIGNEE_SECONDS = 20


def agent_is_ready(user) -> bool:
    if not user or not getattr(user, "is_active", False):
        return False
    if not user.is_call_center():
        return False
    if user_is_whatsapp_call_away(user):
        return False
    if not is_online(user):
        return False
    if not user_is_on_shift_or_unscheduled(user):
        return False
    return True


def ready_agents(company) -> list:
    from django.contrib.auth import get_user_model

    User = get_user_model()
    users = User.objects.filter(
        company=company, role=Role.CALL_CENTER.value, is_active=True
    ).order_by("id")
    return [user for user in users if agent_is_ready(user)]


def next_inbox_agent(company):
    agents = ready_agents(company)
    if not agents:
        return None
    return _pick_round_robin_among_tied(
        company, agents, pointer_field="last_inbox_assigned_agent"
    )


def assign_conversation(conversation: SocialConversation):
    """
    Sticky while the current assignee is ready. Otherwise the next ready agent.
    Nobody ready -> unassigned.
    """
    current = conversation.assigned_to
    if current is not None and agent_is_ready(current):
        return current
    agent = next_inbox_agent(conversation.company)
    if conversation.assigned_to_id == getattr(agent, "id", None):
        return agent
    conversation.assigned_to = agent
    conversation.save(update_fields=["assigned_to", "updated_at"])
    return agent


def drain_unassigned(company) -> int:
    """Give the oldest unassigned open threads to ready agents, round-robin."""
    if not ready_agents(company):
        return 0
    rows = list(
        SocialConversation.objects.filter(
            company=company,
            assigned_to__isnull=True,
            status=WhatsAppConversationStatus.OPEN,
        ).order_by("last_message_at", "id")[:50]
    )
    assigned = 0
    for conversation in rows:
        if assign_conversation(conversation) is not None:
            assigned += 1
    return assigned


def on_agent_became_ready(user_id: int) -> None:
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id, is_active=True).select_related("company").first()
    if not user or not user.company_id or not agent_is_ready(user):
        return
    drain_unassigned(user.company)


def inbox_ring_is_company_wide(call: WhatsAppCall, now=None) -> bool:
    """True once every ready agent should see this inbox ring."""
    if call.ring_escalated_at:
        return True
    assigned_id = getattr(getattr(call, "social_conversation", None), "assigned_to_id", None)
    if not assigned_id:
        return True
    now = now or timezone.now()
    return (now - call.created_at) >= timedelta(seconds=RING_ASSIGNEE_SECONDS)


def visible_inbox_calls(qs, user):
    """Hide an assignee-only ring from everyone else until it escalates."""
    cutoff = timezone.now() - timedelta(seconds=RING_ASSIGNEE_SECONDS)
    private = Q(
        status=WhatsAppCallStatus.RINGING,
        direction=WhatsAppCallDirection.INBOUND,
        social_conversation__assigned_to__isnull=False,
        ring_escalated_at__isnull=True,
        created_at__gt=cutoff,
    ) & ~Q(social_conversation__assigned_to_id=user.id)
    return qs.exclude(private)


def escalate_due_inbox_rings(company_id) -> int:
    """One-time second push when the assignee's 20s window ends. Returns how many."""
    if not company_id:
        return 0
    now = timezone.now()
    cutoff = now - timedelta(seconds=RING_ASSIGNEE_SECONDS)
    due = WhatsAppCall.objects.filter(
        company_id=company_id,
        whatsapp_account__purpose=WhatsAppPurpose.INBOX,
        status=WhatsAppCallStatus.RINGING,
        direction=WhatsAppCallDirection.INBOUND,
        ring_escalated_at__isnull=True,
        created_at__lte=cutoff,
        social_conversation__assigned_to__isnull=False,
    ).select_related("social_conversation", "whatsapp_account", "company")
    sent = 0
    for call in due:
        updated = WhatsAppCall.objects.filter(
            pk=call.pk, ring_escalated_at__isnull=True
        ).update(ring_escalated_at=now)
        if not updated:
            continue
        call.ring_escalated_at = now
        try:
            from integrations.services.whatsapp_call_push import notify_inbound_ringing_call

            notify_inbound_ringing_call(call, escalated=True)
        except Exception:
            pass
        sent += 1
    return sent
