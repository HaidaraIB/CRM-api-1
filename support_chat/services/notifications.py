"""Support chat notification helpers (email for delayed unread + immediate FCM)."""

from __future__ import annotations

import logging

from accounts.event_emails import (
    send_support_chat_unread_to_owner,
    send_support_chat_unread_to_superadmins,
)
from accounts.models import User
from notifications.models import NotificationType
from notifications.services import NotificationService

from ..models import SupportConversation, SupportMessage
from .attachments import media_preview_label

logger = logging.getLogger(__name__)


class SupportChatNotifier:
    @staticmethod
    def notify_owner_unread(conversation: SupportConversation) -> int:
        return send_support_chat_unread_to_owner(conversation)

    @staticmethod
    def notify_agents_unread(conversation: SupportConversation) -> int:
        return send_support_chat_unread_to_superadmins(conversation)


def _message_preview(message: SupportMessage) -> str:
    if getattr(message, "attachment_kind", None):
        preview = media_preview_label(message.attachment_kind, message.body)
    else:
        preview = (message.body or "").strip().replace("\n", " ")
    preview = (preview or "").strip()
    if len(preview) > 160:
        return preview[:157] + "..."
    return preview or "—"


def push_new_message_to_agents(conversation: SupportConversation, message: SupportMessage) -> int:
    """
    Immediate FCM to platform super admins when a tenant owner writes in support chat.
    Push-only (no inbox row) — the admin panel has its own unread UI.

    Note: do not reuse SUPPORT_TICKET_SUPERADMIN_NOTIFY_SKIP_EMAILS here — that list
    only suppresses noisy *email* for shared/test inboxes; push must still reach
    the logged-in admin who registered a browser token.
    """
    company = conversation.company
    company_name = (getattr(company, "name", None) or "").strip() or "Company"
    preview = _message_preview(message)
    sender = message.sender
    sender_id = getattr(sender, "id", None)

    title = f"Support · {company_name}"
    sent = 0
    admins = User.objects.filter(is_superuser=True, is_active=True)
    for admin in list(admins):
        if sender_id and admin.id == sender_id:
            continue
        try:
            if NotificationService.send_notification(
                admin,
                NotificationType.GENERAL.value,
                title=title,
                body=preview,
                data={
                    "kind": "support_chat",
                    "conversation_id": str(conversation.id),
                    "message_id": str(message.id),
                    "company_id": str(conversation.company_id),
                    "invalidate": "support_chat:conversations",
                },
                skip_settings_check=True,
                skip_database_insert=True,
            ):
                sent += 1
        except Exception:
            logger.warning(
                "Support chat agent push failed for user_id=%s",
                admin.id,
                exc_info=True,
            )
    return sent


def push_new_message_to_owner(conversation: SupportConversation, message: SupportMessage) -> int:
    """Immediate FCM to the company owner when LOOP Support replies."""
    owner = getattr(conversation.company, "owner", None)
    if owner is None or not owner.is_active:
        return 0
    sender_id = getattr(message.sender, "id", None)
    if sender_id and owner.id == sender_id:
        return 0

    preview = _message_preview(message)
    lang = (getattr(owner, "language", None) or "en").lower()
    if lang == "ar":
        title = "رسالة من فريق الدعم"
    else:
        title = "Message from LOOP Support"

    try:
        ok = NotificationService.send_notification(
            owner,
            NotificationType.GENERAL.value,
            title=title,
            body=preview,
            data={
                "kind": "support_chat",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "invalidate": "support_chat:messages",
            },
            skip_settings_check=True,
            skip_database_insert=True,
        )
        return 1 if ok else 0
    except Exception:
        logger.warning(
            "Support chat owner push failed for user_id=%s",
            owner.id,
            exc_info=True,
        )
        return 0


def push_support_ticket_to_admins(ticket, creator_user) -> int:
    """Immediate FCM to super admins about a new support ticket (no email-skip list)."""
    company = getattr(ticket, "company", None)
    company_name = (getattr(company, "name", None) or "").strip() or "—"
    creator_id = getattr(creator_user, "id", None)
    title_text = (getattr(ticket, "title", None) or "").strip() or f"Ticket #{ticket.id}"

    sent = 0
    seen_ids = set()
    admins = User.objects.filter(is_superuser=True, is_active=True)
    for admin in list(admins):
        if admin.id in seen_ids:
            continue
        if creator_id and admin.id == creator_id:
            continue
        seen_ids.add(admin.id)
        lang = (getattr(admin, "language", None) or "en").lower()
        if lang == "ar":
            title = f"تذكرة دعم جديدة #{ticket.id}"
            body = f"{company_name}: {title_text}"
        else:
            title = f"New support ticket #{ticket.id}"
            body = f"{company_name}: {title_text}"
        try:
            if NotificationService.send_notification(
                admin,
                NotificationType.GENERAL.value,
                title=title,
                body=body,
                data={
                    "kind": "support_ticket",
                    "ticket_id": str(ticket.id),
                    "company_id": str(getattr(ticket, "company_id", "") or ""),
                    "invalidate": "support:tickets",
                },
                skip_settings_check=True,
                skip_database_insert=True,
            ):
                sent += 1
        except Exception:
            logger.warning(
                "Support ticket push failed for user_id=%s",
                admin.id,
                exc_info=True,
            )
    return sent
