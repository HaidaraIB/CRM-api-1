"""WhatsApp conversation triage state helpers (open/pending/spam/… + snooze)."""

from __future__ import annotations

from django.utils import timezone

from integrations.models import (
    LeadWhatsAppMessage,
    WhatsAppConversationState,
    WhatsAppConversationStatus,
)


def ensure_conversation_state(client) -> WhatsAppConversationState:
    """get_or_create state for a client; company is taken from the client."""
    state, _ = WhatsAppConversationState.objects.get_or_create(
        client=client,
        defaults={"company_id": client.company_id},
    )
    return state


def apply_inbound_message_rules(message: LeadWhatsAppMessage) -> None:
    """
    On inbound message: reopen Done/Snoozed to Open.
    Spam, Invalid, and Pending are sticky. Missing row is already Open — no-op.
    """
    if message.direction != LeadWhatsAppMessage.DIRECTION_INBOUND:
        return
    client = message.client
    if client is None:
        return
    try:
        state = WhatsAppConversationState.objects.get(client_id=client.pk)
    except WhatsAppConversationState.DoesNotExist:
        return
    if state.status not in (
        WhatsAppConversationStatus.DONE,
        WhatsAppConversationStatus.SNOOZED,
    ):
        return
    state.status = WhatsAppConversationStatus.OPEN
    state.snoozed_until = None
    state.status_changed_at = timezone.now()
    state.save(update_fields=["status", "snoozed_until", "status_changed_at", "updated_at"])


def sweep_expired_snoozes(company) -> int:
    """
    Reopen snoozed conversations whose snoozed_until has passed.
    Uses queryset.update (skips signals) — caller must bump chat slice if needed.
    """
    now = timezone.now()
    return WhatsAppConversationState.objects.filter(
        company=company,
        status=WhatsAppConversationStatus.SNOOZED,
        snoozed_until__lte=now,
    ).update(status=WhatsAppConversationStatus.OPEN, snoozed_until=None)
