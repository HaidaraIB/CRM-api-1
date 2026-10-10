"""Triage-state helpers for the Omni-Channel Inbox (mirror of whatsapp_conversation_state)."""

from __future__ import annotations

from django.utils import timezone

from .models import SocialConversation, WhatsAppConversationStatus

# Statuses an agent chose deliberately and which an incoming message must not undo.
STICKY_STATUSES = frozenset(
    {
        WhatsAppConversationStatus.SPAM,
        WhatsAppConversationStatus.INVALID,
        WhatsAppConversationStatus.PENDING,
    }
)


def sweep_expired_snoozes(company) -> int:
    """
    Reopen conversations whose snooze has elapsed.

    Run before the list view mints its ETag: a snooze expiring is a change to
    what the list shows, but nothing writes a row when the clock passes, so
    without this the list would 304 with the conversation still hidden.
    """
    if company is None:
        return 0
    now = timezone.now()
    expired = SocialConversation.objects.filter(
        company=company,
        status=WhatsAppConversationStatus.SNOOZED,
        snoozed_until__isnull=False,
        snoozed_until__lte=now,
    )
    updated = expired.update(
        status=WhatsAppConversationStatus.OPEN,
        snoozed_until=None,
        status_changed_at=now,
    )
    # queryset.update skips signals. Bump here so every caller (the list view
    # and any future cron) publishes the reopen.
    if updated and getattr(company, "id", None):
        from sync.version import bump_company_slice

        bump_company_slice("inbox", company.id)
    return updated
