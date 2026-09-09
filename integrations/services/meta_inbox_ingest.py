"""
Webhook ingestion for Instagram Direct + Facebook Messenger.

Design notes worth keeping in mind when editing:

* Conversations are lead-less. Nothing here creates a crm.Client — that only
  happens when an agent explicitly converts (see services/social_lead.py).
* Every handler is defensive and never raises to the view. Meta disables a
  webhook that 5xx's, so a single malformed event must not take the endpoint down.
* Tenant identity comes only from entry[].id, which is why page_id/ig_user_id are
  globally unique on MetaInboxConnection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone

from settings.models import SystemSettings

from ..models import (
    IntegrationLog,
    MetaInboxConnection,
    SocialChannel,
    SocialContact,
    SocialConversation,
    SocialMessage,
)
from ..policy import get_effective_integration_policy, get_plan_integration_access
from .meta_inbox_media import apply_attachment_to_message

logger = logging.getLogger(__name__)

PREVIEW_LABELS = {
    'image': 'Photo',
    'video': 'Video',
    'audio': 'Voice message',
    'document': 'File',
    'location': 'Location',
    'share': 'Shared post',
    'story_mention': 'Story mention',
    'reel': 'Reel',
}


def resolve_connection(webhook_object: str, entry_id: str) -> MetaInboxConnection | None:
    """
    Map entry[].id to a connected Page.

    For object=instagram the id is the IGID; for object=page it is the Page id.
    Some Instagram payloads carry the Page id instead, so the IG lookup falls
    back to page_id rather than dropping the message.
    """
    entry_id = str(entry_id or '').strip()
    if not entry_id:
        return None

    qs = MetaInboxConnection.objects.filter(status='connected')
    if webhook_object == 'instagram':
        return (
            qs.filter(ig_user_id=entry_id).first()
            or qs.filter(page_id=entry_id).first()
        )
    return qs.filter(page_id=entry_id).first()


def integration_allowed(company) -> bool:
    """Plan + admin policy gate, matching what the HTTP endpoints enforce."""
    if not get_plan_integration_access(company, 'meta_inbox')['enabled']:
        return False
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform='meta_inbox',
    )
    return bool(effective['enabled'])


def _meta_timestamp(value) -> datetime | None:
    """Meta sends epoch milliseconds. Authoritative for the 24h window."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def preview_for(kind: str | None, body: str | None) -> str:
    text = (body or '').strip()
    if text:
        return text[:280]
    return PREVIEW_LABELS.get(kind or '', '')


def get_or_create_contact(connection, channel: str, external_id: str) -> SocialContact:
    """Idempotent under the (company, channel, external_id) unique constraint."""
    try:
        with transaction.atomic():
            contact, _ = SocialContact.objects.get_or_create(
                company=connection.company,
                channel=channel,
                external_id=external_id,
                defaults={'connection': connection},
            )
            return contact
    except IntegrityError:
        # Concurrent webhook deliveries for a first-time sender.
        return SocialContact.objects.get(
            company=connection.company, channel=channel, external_id=external_id
        )


def get_or_create_conversation(connection, contact) -> SocialConversation:
    try:
        with transaction.atomic():
            conversation, _ = SocialConversation.objects.get_or_create(
                connection=connection,
                contact=contact,
                defaults={
                    'company': connection.company,
                    'channel': contact.channel,
                },
            )
            return conversation
    except IntegrityError:
        return SocialConversation.objects.get(connection=connection, contact=contact)


def update_conversation_from_message(conversation, message) -> None:
    """
    Roll the denormalized list-row fields forward.

    Only inbound messages move last_inbound_at (the 24h anchor) or the unread
    count — an echo of the business's own reply must not mark the thread unread.
    """
    fields = ['last_message_at', 'last_message_direction', 'last_message_preview', 'updated_at']
    stamp = message.sent_at or message.created_at or timezone.now()

    conversation.last_message_at = stamp
    conversation.last_message_direction = message.direction
    conversation.last_message_preview = preview_for(message.attachment_kind, message.body)

    if message.direction == SocialMessage.DIRECTION_INBOUND:
        conversation.last_inbound_at = stamp
        conversation.unread_count = (conversation.unread_count or 0) + 1
        fields += ['last_inbound_at', 'unread_count']

        # An inbound message reopens a resolved thread. Spam/Invalid/Pending are
        # sticky — an agent's triage decision should survive the sender pinging
        # again, which is exactly what makes those states useful.
        from ..models import WhatsAppConversationStatus

        if conversation.status in (
            WhatsAppConversationStatus.DONE,
            WhatsAppConversationStatus.SNOOZED,
        ):
            conversation.status = WhatsAppConversationStatus.OPEN
            conversation.snoozed_until = None
            fields += ['status', 'snoozed_until']

    conversation.save(update_fields=fields)


def _log(connection, action: str, status: str, message: str, data: dict | None = None) -> None:
    account = getattr(connection, 'integration_account', None)
    if not account:
        return
    try:
        IntegrationLog.objects.create(
            account=account,
            action=action,
            status=status,
            message=message,
            response_data=data or {},
        )
    except Exception:
        logger.exception("Meta Inbox: failed writing IntegrationLog")


def process_messaging_event(
    webhook_object: str,
    connection: MetaInboxConnection,
    event: dict,
    *,
    is_standby: bool = False,
) -> SocialMessage | None:
    """Route one entry[].messaging[] item. Returns the created message, if any."""
    if not isinstance(event, dict):
        return None

    channel = (
        SocialChannel.INSTAGRAM if webhook_object == 'instagram' else SocialChannel.MESSENGER
    )
    sender_id = str(((event.get('sender') or {}).get('id')) or '').strip()
    recipient_id = str(((event.get('recipient') or {}).get('id')) or '').strip()
    if not sender_id:
        return None

    business_ids = {
        str(connection.page_id or ''),
        str(connection.ig_user_id or ''),
    } - {''}

    message = event.get('message') if isinstance(event.get('message'), dict) else None
    is_echo = bool(message and message.get('is_echo'))
    outbound = is_echo or sender_id in business_ids

    # The customer is whichever side is not the business.
    contact_external_id = recipient_id if outbound else sender_id
    if not contact_external_id or contact_external_id in business_ids:
        # Direction was misread (or an id was missing) — take whichever side is
        # demonstrably not ours rather than filing the thread under our own id.
        candidates = [i for i in (sender_id, recipient_id) if i and i not in business_ids]
        contact_external_id = candidates[0] if candidates else ''
    if not contact_external_id:
        return None

    contact = get_or_create_contact(connection, channel, contact_external_id)
    conversation = get_or_create_conversation(connection, contact)

    if event.get('reaction'):
        _apply_reaction(conversation, event.get('reaction'))
        return None
    if 'read' in event or 'delivery' in event:
        _apply_delivery_or_read(conversation, event)
        return None
    if event.get('postback'):
        return _store_postback(conversation, event, outbound=outbound)
    if not message:
        return None

    return _store_message(
        conversation,
        event,
        message,
        outbound=outbound,
        is_echo=is_echo,
        is_standby=is_standby,
    )


def _store_message(conversation, event, message, *, outbound, is_echo, is_standby):
    mid = str(message.get('mid') or '').strip()
    body = message.get('text') or ''
    sent_at = _meta_timestamp(event.get('timestamp'))
    direction = (
        SocialMessage.DIRECTION_OUTBOUND if outbound else SocialMessage.DIRECTION_INBOUND
    )

    # An echo of a message we sent ourselves already has a row — update it rather
    # than duplicating the agent's bubble.
    if mid:
        existing = SocialMessage.objects.filter(
            conversation=conversation, external_message_id=mid
        ).first()
        if existing:
            if is_echo and existing.delivery_status in (None, '', 'pending'):
                existing.delivery_status = 'sent'
                existing.save(update_fields=['delivery_status'])
            return None

    row = SocialMessage(
        conversation=conversation,
        direction=direction,
        external_message_id=mid,
        body=body,
        is_echo=is_echo,
        is_read=outbound,
        sent_at=sent_at,
        delivery_status='sent' if outbound else None,
    )

    for attachment in message.get('attachments') or []:
        try:
            apply_attachment_to_message(row, attachment)
        except Exception:
            logger.exception("Meta Inbox: attachment handling failed for mid=%s", mid)
        # v1 stores the first attachment per message; Meta splits multi-attachment
        # sends into separate events in practice.
        break

    try:
        row.save()
    except IntegrityError:
        # Concurrent redelivery of the same mid.
        return None

    update_conversation_from_message(conversation, row)

    if is_standby:
        logger.info(
            "Meta Inbox: standby event stored (app is not primary receiver) conv=%s",
            conversation.id,
        )

    # Echoes are the business replying from their own phone — pushing that back
    # to the agent who is already looking at the thread is noise.
    if row.direction == SocialMessage.DIRECTION_INBOUND:
        from .social_push import notify_social_inbound

        notify_social_inbound(conversation=conversation, message=row)

    return row


def _store_postback(conversation, event, *, outbound):
    postback = event.get('postback') or {}
    body = postback.get('title') or postback.get('payload') or ''
    mid = str(postback.get('mid') or '').strip()
    if mid and SocialMessage.objects.filter(
        conversation=conversation, external_message_id=mid
    ).exists():
        return None

    row = SocialMessage(
        conversation=conversation,
        direction=(
            SocialMessage.DIRECTION_OUTBOUND if outbound else SocialMessage.DIRECTION_INBOUND
        ),
        external_message_id=mid,
        body=body,
        is_read=outbound,
        sent_at=_meta_timestamp(event.get('timestamp')),
    )
    try:
        row.save()
    except IntegrityError:
        return None
    update_conversation_from_message(conversation, row)
    return row


def _apply_reaction(conversation, reaction) -> None:
    """Reactions annotate an existing bubble; they are not messages of their own."""
    if not isinstance(reaction, dict):
        return
    mid = str(reaction.get('mid') or '').strip()
    if not mid:
        return
    value = '' if reaction.get('action') == 'unreact' else (
        reaction.get('emoji') or reaction.get('reaction') or ''
    )
    SocialMessage.objects.filter(
        conversation=conversation, external_message_id=mid
    ).update(reaction=value[:32])


def _apply_delivery_or_read(conversation, event) -> None:
    delivery = event.get('delivery') if isinstance(event.get('delivery'), dict) else None
    if not delivery:
        # `read`/`messaging_seen` carries no per-message id worth persisting.
        return
    mids = [str(m).strip() for m in (delivery.get('mids') or []) if str(m).strip()]
    if not mids:
        return
    SocialMessage.objects.filter(
        conversation=conversation,
        external_message_id__in=mids,
        direction=SocialMessage.DIRECTION_OUTBOUND,
    ).exclude(delivery_status='failed').update(delivery_status='delivered')


def process_entry(webhook_object: str, entry: dict) -> int:
    """
    Handle one entry[]. Returns how many new messages were stored.

    Never raises — the caller must always answer Meta with 200.
    """
    if not isinstance(entry, dict):
        return 0

    entry_id = str(entry.get('id') or '').strip()
    connection = resolve_connection(webhook_object, entry_id)
    if not connection:
        # No account to attach an IntegrationLog to, so a warning is all we can do.
        logger.warning(
            "Meta Inbox: unresolved tenant for object=%s entry_id=%s",
            webhook_object,
            entry_id,
        )
        return 0

    if not integration_allowed(connection.company):
        logger.info(
            "Meta Inbox: dropping event, integration disabled for company %s",
            connection.company_id,
        )
        return 0

    stored = 0
    events = list(entry.get('messaging') or [])
    standby_events = list(entry.get('standby') or [])

    for event in events:
        try:
            if process_messaging_event(webhook_object, connection, event):
                stored += 1
        except Exception:
            logger.exception("Meta Inbox: failed processing messaging event")

    for event in standby_events:
        try:
            if process_messaging_event(webhook_object, connection, event, is_standby=True):
                stored += 1
        except Exception:
            logger.exception("Meta Inbox: failed processing standby event")

    if stored:
        MetaInboxConnection.objects.filter(pk=connection.pk).update(
            last_webhook_at=timezone.now()
        )
        _log(
            connection,
            'meta_inbox_message_received',
            'success',
            f"Stored {stored} message(s) from {webhook_object}",
            {'object': webhook_object, 'entry_id': entry_id, 'count': stored},
        )

    return stored
