"""
Inbound WhatsApp Cloud messages for the Omni-Channel Inbox (lead-less).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone

from settings.models import SystemSettings
from sync.version import bump_company_slice

from ..models import (
    SocialChannel,
    SocialContact,
    SocialConversation,
    SocialMessage,
    WhatsAppInboxNumber,
)
from ..policy import get_effective_integration_policy, get_plan_integration_access
from .meta_inbox_ingest import preview_for, update_conversation_from_message
from .social_push import notify_social_inbound

logger = logging.getLogger(__name__)


def _wa_timestamp(value) -> datetime | None:
    try:
        sec = int(value)
    except (TypeError, ValueError):
        return None
    if sec <= 0:
        return None
    try:
        return datetime.fromtimestamp(sec, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def inbox_allowed(company) -> bool:
    if not get_plan_integration_access(company, 'meta_inbox')['enabled']:
        return False
    if not get_plan_integration_access(company, 'whatsapp')['enabled']:
        return False
    for platform in ('meta_inbox', 'whatsapp'):
        effective = get_effective_integration_policy(
            SystemSettings.get_settings().integration_policies or {},
            company_id=company.id,
            platform=platform,
        )
        if not effective['enabled']:
            return False
    return True


def get_or_create_contact(inbox_number: WhatsAppInboxNumber, wa_id: str) -> SocialContact:
    wa_id = str(wa_id or '').strip()
    try:
        with transaction.atomic():
            contact, _ = SocialContact.objects.get_or_create(
                company=inbox_number.company,
                channel=SocialChannel.WHATSAPP,
                external_id=wa_id,
                defaults={'wa_inbox_number': inbox_number},
            )
            return contact
    except IntegrityError:
        return SocialContact.objects.get(
            company=inbox_number.company,
            channel=SocialChannel.WHATSAPP,
            external_id=wa_id,
        )


def get_or_create_conversation(
    inbox_number: WhatsAppInboxNumber, contact: SocialContact
) -> SocialConversation:
    try:
        with transaction.atomic():
            conversation, _ = SocialConversation.objects.get_or_create(
                wa_inbox_number=inbox_number,
                contact=contact,
                defaults={
                    'company': inbox_number.company,
                    'channel': SocialChannel.WHATSAPP,
                },
            )
            return conversation
    except IntegrityError:
        return SocialConversation.objects.get(
            wa_inbox_number=inbox_number, contact=contact
        )


def process_whatsapp_inbox_message(inbox_number: WhatsAppInboxNumber, message: dict) -> None:
    if inbox_number.status != 'connected':
        return
    company = inbox_number.company
    if not inbox_allowed(company):
        logger.info(
            'WhatsApp inbox inbound ignored (gated) company_id=%s phone_number_id=%s',
            company.id,
            inbox_number.phone_number_id,
        )
        return

    from_number = str(message.get('from') or '').strip()
    message_id = str(message.get('id') or '').strip()
    if not from_number:
        return

    message_type = message.get('type') or 'text'
    if message_type == 'text':
        text_body = (message.get('text') or {}).get('body', '')
    else:
        from integrations.services.whatsapp_media import media_body_from_meta_message

        text_body = media_body_from_meta_message(message) or f"[{message_type}]"

    contact = get_or_create_contact(inbox_number, from_number)
    if not contact.name:
        contact.name = from_number
        contact.save(update_fields=['name', 'updated_at'])
    conversation = get_or_create_conversation(inbox_number, contact)

    if message_id:
        existing = SocialMessage.objects.filter(
            conversation=conversation, external_message_id=message_id
        ).first()
        if existing:
            return

    sent_at = _wa_timestamp(message.get('timestamp')) or timezone.now()
    attachment_kind = None
    if message_type in ('image', 'video', 'audio', 'document', 'sticker'):
        attachment_kind = 'image' if message_type == 'sticker' else message_type

    row = SocialMessage.objects.create(
        conversation=conversation,
        direction=SocialMessage.DIRECTION_INBOUND,
        body=text_body,
        external_message_id=message_id,
        sent_at=sent_at,
        is_read=False,
        attachment_kind=attachment_kind,
    )

    token = inbox_number.get_access_token()
    if token:
        try:
            from integrations.services.whatsapp_media import (
                apply_meta_media_to_message,
                extract_meta_media_info,
            )

            if extract_meta_media_info(message):
                apply_meta_media_to_message(row, message, access_token=token)
                row.save()
        except Exception:
            logger.exception('WhatsApp inbox: media ingest failed conv=%s', conversation.id)

    update_conversation_from_message(conversation, row)
    inbox_number.last_webhook_at = timezone.now()
    inbox_number.save(update_fields=['last_webhook_at', 'updated_at'])
    bump_company_slice('inbox', company.id)
    try:
        notify_social_inbound(conversation=conversation, message=row)
    except Exception:
        logger.exception('WhatsApp inbox: push failed conv=%s', conversation.id)


def process_whatsapp_inbox_status(inbox_number: WhatsAppInboxNumber, status_obj: dict) -> None:
    wam_id = str(status_obj.get('id') or '').strip()
    if not wam_id:
        return
    delivery = (status_obj.get('status') or '').strip().lower()
    if not delivery:
        return
    qs = SocialMessage.objects.filter(
        external_message_id=wam_id,
        conversation__wa_inbox_number=inbox_number,
    )
    msg = qs.first()
    if not msg:
        return
    msg.delivery_status = delivery
    msg.save(update_fields=['delivery_status'])
