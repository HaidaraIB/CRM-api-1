"""FCM push when an inbound Instagram/Messenger message is stored from the webhook."""

from __future__ import annotations

import logging

from notifications.models import NotificationType
from notifications.services import NotificationService

logger = logging.getLogger(__name__)


def _social_inbound_recipients(conversation) -> list:
    """
    Whoever owns the conversation right now.

    Converted threads follow the lead's current assignee (employees/doctors only
    see those). Unconverted DMs have no lead, so the call-center pool is the
    natural fallback — triaging these is precisely that role's job.
    """
    client = getattr(conversation, 'client', None)
    if client is not None and client.assigned_to_id:
        lead_assignee = client.assigned_to
        if lead_assignee and getattr(lead_assignee, 'is_active', True):
            return [lead_assignee]

    company = getattr(conversation, 'company', None)
    if company is None:
        return []

    from accounts.models import Role, User

    call_center = list(
        User.objects.filter(
            company=company,
            role=Role.CALL_CENTER.value,
            is_active=True,
        )[:10]
    )
    if call_center:
        return call_center

    owner = getattr(company, 'owner', None)
    if owner and getattr(owner, 'is_active', True):
        return [owner]
    return []


def notify_social_inbound(*, conversation, message) -> None:
    """
    Best-effort push. Failures are logged only — webhook ingestion must never
    depend on push delivery.

    skip_database_insert mirrors notify_whatsapp_inbound: the Inbox unread badge
    covers the web surface, so a bell row per DM would be pure noise.
    """
    try:
        from ..models import SocialConversation

        conversation = (
            SocialConversation.objects.select_related(
                'assigned_to', 'client', 'client__assigned_to', 'company', 'company__owner', 'contact'
            ).get(pk=conversation.pk)
        )

        recipients = _social_inbound_recipients(conversation)
        if not recipients:
            logger.info(
                "Social inbound push skipped: no recipient conversation_id=%s company_id=%s",
                conversation.id,
                conversation.company_id,
            )
            return

        preview = (message.body or '').strip().replace('\n', ' ')
        if not preview:
            preview = conversation.last_message_preview or ''
        if len(preview) > 160:
            preview = preview[:157] + '...'

        contact = conversation.contact
        title = contact.display_name if contact else conversation.get_channel_display()

        data = {
            'conversation_id': str(conversation.id),
            'channel': conversation.channel,
            'contact_name': title,
            'message_id': message.external_message_id or '',
            'message_preview': preview,
            'invalidate': 'social:conversations',
            'client_id': str(conversation.client_id or ''),
        }

        for user in recipients:
            NotificationService.send_notification(
                user=user,
                notification_type=NotificationType.SOCIAL_MESSAGE_RECEIVED,
                title=title,
                body=preview or title,
                data=data,
                lead_source=conversation.channel,
                skip_database_insert=True,
            )
    except Exception:
        logger.exception(
            "Social inbound push failed conversation_id=%s",
            getattr(conversation, 'id', None),
        )
