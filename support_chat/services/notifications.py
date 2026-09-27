"""Support chat notification helpers (email for delayed unread)."""

from __future__ import annotations

from accounts.event_emails import (
    send_support_chat_unread_to_owner,
    send_support_chat_unread_to_superadmins,
)

from ..models import SupportConversation


class SupportChatNotifier:
    @staticmethod
    def notify_owner_unread(conversation: SupportConversation) -> int:
        return send_support_chat_unread_to_owner(conversation)

    @staticmethod
    def notify_agents_unread(conversation: SupportConversation) -> int:
        return send_support_chat_unread_to_superadmins(conversation)
