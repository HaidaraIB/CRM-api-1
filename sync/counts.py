"""Count helpers reused by GET /sync/digest/ — same querysets as the original endpoints."""

from __future__ import annotations

from django.db.models import F, OuterRef, Q, Subquery

from integrations.models import LeadWhatsAppMessage, WhatsAppCallDirection, WhatsAppCallStatus
from integrations.policy import get_plan_integration_access, get_effective_integration_policy
from integrations.views.webhooks_messaging import _integration_gate as whatsapp_policy_gate
from integrations.views.whatsapp_calling import _company_calls_qs
from integrations.whatsapp_access import (
    filter_whatsapp_messages_queryset,
    user_can_access_whatsapp_calls,
    user_can_access_whatsapp_chats,
)
from integrations.services.whatsapp_call_availability import user_is_whatsapp_call_away
from notifications.models import Notification, NotificationType
from notifications.views import exclude_inbox_noise_notifications
from platform_content.models import NewsPost, UserNewsReadState
from settings.models import SystemSettings
from tenant_chat.authorization import chat_role_bucket
from tenant_chat.models import ChatConversation, ChatConversationReadState, ChatMessage
def whatsapp_unread_for_user(user):
    """None when gated (plan/policy/user); otherwise unread inbound count."""
    company = getattr(user, "company", None)
    if not company:
        return None
    plan_gate = get_plan_integration_access(company, "whatsapp")
    if not plan_gate["enabled"]:
        return None
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform="whatsapp",
    )
    if not effective["enabled"]:
        return None
    if not user_can_access_whatsapp_chats(user):
        return None
    qs = LeadWhatsAppMessage.objects.filter(
        client__company=company,
        direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        is_read=False,
    )
    qs = filter_whatsapp_messages_queryset(user, qs)
    return qs.count()


def whatsapp_calls_pending_for_user(user):
    """None when gated; otherwise count of pending ringing calls (same as /calls/pending/)."""
    company = getattr(user, "company", None)
    if not company:
        return None
    gate = whatsapp_policy_gate(company, "whatsapp")
    if not gate.get("enabled"):
        return None
    if not user_can_access_whatsapp_calls(user):
        return None

    seen = set()
    if not user_is_whatsapp_call_away(user):
        qs = (
            _company_calls_qs(user)
            .filter(
                status=WhatsAppCallStatus.RINGING,
                direction=WhatsAppCallDirection.INBOUND,
                agent__isnull=True,
            )
            .filter(Q(client__isnull=True) | Q(client__assigned_to_id=user.id))
            .order_by("created_at")[:20]
        )
        for call in qs:
            seen.add(call.id)
    mine = (
        _company_calls_qs(user)
        .filter(
            status=WhatsAppCallStatus.RINGING,
            agent_id=user.id,
        )
        .order_by("-created_at")[:10]
    )
    for call in mine:
        seen.add(call.id)
    return len(seen)


def tenant_chat_unread_for_user(user) -> int:
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    if user.is_super_admin() or not getattr(user, "company_id", None):
        return 0
    bucket = chat_role_bucket(user)
    if bucket != "ineligible":
        conv_filter = (
            Q(kind=ChatConversation.Kind.COMPANY_GROUP)
            | Q(participant_low=user)
            | Q(participant_high=user)
        )
    else:
        conv_filter = Q(participant_low=user) | Q(participant_high=user)
    conv_ids = ChatConversation.objects.filter(
        conv_filter, company_id=user.company_id
    ).values("id")
    last_read = ChatConversationReadState.objects.filter(
        conversation_id=OuterRef("conversation_id"),
        user_id=user.id,
    ).values("last_read_message_id")[:1]
    qs = ChatMessage.objects.filter(conversation_id__in=conv_ids).exclude(sender_id=user.id)
    qs = qs.annotate(_last_read=Subquery(last_read)).filter(
        Q(_last_read__isnull=True) | Q(id__gt=F("_last_read"))
    )
    return qs.count()


def notifications_unread_for_user(user) -> int:
    return exclude_inbox_noise_notifications(
        Notification.objects.filter(
            user=user,
            read=False,
            deleted_at__isnull=True,
        )
    ).count()


def news_unread_for_user(user) -> int:
    qs = NewsPost.objects.filter(is_published=True, published_at__isnull=False)
    try:
        state = user.news_read_state
        qs = qs.filter(published_at__gt=state.last_read_at)
    except UserNewsReadState.DoesNotExist:
        pass
    return qs.count()


def arrivals_pending_for_user(user) -> int:
    """Unacknowledged walk-in arrivals addressed to this user (any role)."""
    from datetime import timedelta

    from django.utils import timezone

    from crm.models import LeadArrival

    if not user or not getattr(user, "company_id", None):
        return 0
    return LeadArrival.objects.filter(
        company_id=user.company_id,
        notified_users=user,
        acknowledged_at__isnull=True,
        announced_at__gte=timezone.now() - timedelta(hours=2),
    ).distinct().count()


def arrivals_waiting_for_user(user) -> int:
    """Company-wide unacknowledged arrivals today; only meaningful for the front desk /
    owner / manage_leads supervisors — everyone else gets 0 (this badges the board, not
    a personal inbox, so scoping mirrors LeadArrivalViewSet's company-wide read branch)."""
    from crm.models import LeadArrival
    from crm.availability import local_now_for_company

    if not user or not getattr(user, "company_id", None):
        return 0
    company = getattr(user, "company", None)
    can_see_board = (
        user.is_admin()
        or user.is_reception()
        or user.is_call_center()
        or (user.is_supervisor() and user.supervisor_has_permission("manage_leads"))
    )
    if not can_see_board:
        return 0
    local_today = local_now_for_company(company).date()
    return LeadArrival.objects.filter(
        company_id=user.company_id,
        acknowledged_at__isnull=True,
        announced_at__date=local_today,
    ).count()


def pbx_screen_pop_for_user(user):
    n = (
        Notification.objects.filter(
            user=user,
            type=NotificationType.PBX_INCOMING_CALL,
            read=False,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if n is None:
        return None
    data = n.data if isinstance(n.data, dict) else {}
    client_id = data.get("client_id") or data.get("lead_id")
    try:
        client_id = int(client_id) if client_id is not None else None
    except (TypeError, ValueError):
        client_id = None
    return {"notification_id": n.id, "client_id": client_id}
