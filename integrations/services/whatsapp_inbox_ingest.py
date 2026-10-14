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
    WhatsAppAccount,
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


def get_or_create_contact(
    inbox_number: WhatsAppAccount, wa_id: str, wa_user_id: str | None = None
) -> SocialContact:
    """
    Match a WhatsApp sender to one contact.

    ``wa_user_id`` (BSUID) is checked first, then the phone. A later message that
    carries both fills the missing id so a username-only thread and a phone
    thread do not split into two people.
    """
    wa_id = str(wa_id or '').strip()
    wa_user_id = str(wa_user_id or '').strip()
    company = inbox_number.company
    contact = None
    if wa_user_id:
        contact = SocialContact.objects.filter(
            company=company,
            channel=SocialChannel.WHATSAPP,
            wa_user_id=wa_user_id,
        ).first()
    if contact is None and wa_id:
        contact = SocialContact.objects.filter(
            company=company,
            channel=SocialChannel.WHATSAPP,
            external_id=wa_id,
        ).first()
    if contact is None:
        external = wa_id or wa_user_id
        if not external:
            raise ValueError('WhatsApp contact needs a phone or user id')
        try:
            with transaction.atomic():
                contact, _created = SocialContact.objects.get_or_create(
                    company=company,
                    channel=SocialChannel.WHATSAPP,
                    external_id=external,
                    defaults={
                        'wa_inbox_number': inbox_number,
                        'wa_user_id': wa_user_id,
                    },
                )
        except IntegrityError:
            contact = SocialContact.objects.get(
                company=company,
                channel=SocialChannel.WHATSAPP,
                external_id=external,
            )
    updates: list[str] = []
    if wa_user_id and contact.wa_user_id != wa_user_id:
        taken = SocialContact.objects.filter(
            company=company,
            channel=SocialChannel.WHATSAPP,
            wa_user_id=wa_user_id,
        ).exclude(pk=contact.pk).exists()
        if not taken:
            contact.wa_user_id = wa_user_id
            updates.append('wa_user_id')
    if wa_id and contact.external_id != wa_id:
        taken = SocialContact.objects.filter(
            company=company,
            channel=SocialChannel.WHATSAPP,
            external_id=wa_id,
        ).exclude(pk=contact.pk).exists()
        if not taken:
            contact.external_id = wa_id
            updates.append('external_id')
    if updates:
        updates.append('updated_at')
        contact.save(update_fields=updates)
    return contact


def get_or_create_conversation(
    inbox_number: WhatsAppAccount, contact: SocialContact
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


def process_whatsapp_inbox_message(
    inbox_number: WhatsAppAccount, message: dict, profile_name: str | None = None
) -> None:
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
    user_id = str(message.get('from_user_id') or '').strip()
    message_id = str(message.get('id') or '').strip()
    if not from_number and not user_id:
        return

    message_type = message.get('type') or 'text'
    if message_type == 'text':
        text_body = (message.get('text') or {}).get('body', '')
    else:
        from integrations.services.whatsapp_media import media_body_from_meta_message

        text_body = media_body_from_meta_message(message) or f"[{message_type}]"

    contact = get_or_create_contact(inbox_number, from_number, wa_user_id=user_id)
    # Webhook contacts[].profile.name only — never stamp the phone as name
    # (that permanently shadows display_name's +E.164 formatting).
    # Respect agent overrides (name_manually_set).
    profile_name = str(profile_name or "").strip()
    if (
        profile_name
        and not contact.name_manually_set
        and contact.name != profile_name
    ):
        contact.name = profile_name
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


def process_whatsapp_inbox_status(inbox_number: WhatsAppAccount, status_obj: dict) -> None:
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
