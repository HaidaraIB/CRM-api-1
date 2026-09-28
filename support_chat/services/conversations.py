from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from companies.models import Company
from sync.cache import invalidate_badges
from sync.version import bump_company_slice, bump_support_conversation

from ..models import SupportConversation, SupportMessage
from .attachments import (
    KIND_IMAGE,
    build_object_key,
    chat_storage,
    image_upload_pixel_dimensions,
    is_configured,
    is_supabase_chat_storage,
    is_supabase_mode_requested,
    media_preview_label,
    normalize_chat_image_upload,
    safe_original_filename,
    validate_uploaded_file,
)

logger = logging.getLogger(__name__)


def _preview_for_message(msg: SupportMessage) -> str:
    if getattr(msg, "attachment_kind", None):
        return media_preview_label(msg.attachment_kind, msg.body)[:200]
    body = (msg.body or "").strip().replace("\n", " ")
    return body[:200] if body else "Message"


def get_or_create_for_company(company: Company) -> tuple[SupportConversation, bool]:
    return SupportConversation.objects.get_or_create(company=company)


def send_message(
    conversation: SupportConversation,
    sender,
    side: str,
    body: str = "",
    uploaded_file=None,
    reply_to_id: int | None = None,
) -> SupportMessage:
    reply_to = None
    if reply_to_id is not None:
        reply_to = SupportMessage.objects.filter(
            conversation=conversation, pk=reply_to_id
        ).first()
        if reply_to is None:
            raise ValueError("Reply target not found in this conversation.")

    if uploaded_file and not (body or "").strip():
        body = ""

    processed = None
    mime_final = ""
    size_final = 0
    kind = None
    name_hint = ""
    attachment_width = None
    attachment_height = None

    if uploaded_file:
        kind, mime_final, size_final = validate_uploaded_file(uploaded_file)
        if kind == KIND_IMAGE:
            try:
                normalized = normalize_chat_image_upload(uploaded_file)
                if normalized:
                    processed, mime_final, size_final, attachment_width, attachment_height = normalized
                else:
                    dims = image_upload_pixel_dimensions(uploaded_file)
                    if dims:
                        attachment_width, attachment_height = dims
            except ValueError:
                raise
        name_hint = (
            processed.name if processed is not None else safe_original_filename(uploaded_file.name)
        )

    if is_supabase_mode_requested() and not is_configured():
        if uploaded_file:
            raise ValueError("Chat storage is misconfigured.")

    company_id = conversation.company_id

    with transaction.atomic():
        conversation = SupportConversation.objects.select_for_update().get(pk=conversation.pk)

        if conversation.status == SupportConversation.Status.RESOLVED:
            conversation.status = SupportConversation.Status.OPEN
            conversation.resolved_at = None
            conversation.resolved_by_id = None

        if uploaded_file:
            msg = SupportMessage.objects.create(
                conversation=conversation,
                sender=sender,
                side=side,
                body=body or "",
                reply_to=reply_to,
                attachment_kind=kind,
                attachment_mime=mime_final,
                attachment_size=size_final,
                attachment_width=attachment_width,
                attachment_height=attachment_height,
                original_filename=safe_original_filename(uploaded_file.name),
            )
            if is_supabase_chat_storage():
                key = build_object_key(company_id, msg.id, name_hint)
                if processed is not None:
                    payload = processed.read()
                else:
                    uploaded_file.seek(0)
                    payload = uploaded_file.read()
                chat_storage.upload_bytes(key, payload, mime_final)
                msg.attachment_object_key = key
                msg.save(update_fields=["attachment_object_key"])
            else:
                if processed is not None:
                    msg.attachment.save(processed.name, processed, save=True)
                else:
                    uploaded_file.seek(0)
                    msg.attachment.save(
                        safe_original_filename(uploaded_file.name), uploaded_file, save=True
                    )
        else:
            if not (body or "").strip():
                raise ValueError("Message body or attachment is required.")
            msg = SupportMessage.objects.create(
                conversation=conversation,
                sender=sender,
                side=side,
                body=body,
                reply_to=reply_to,
            )

        conversation.last_message_at = msg.created_at
        conversation.last_message_side = side
        conversation.last_message_preview = _preview_for_message(msg)
        conversation.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "last_message_at",
                "last_message_side",
                "last_message_preview",
                "updated_at",
            ]
        )

    bump_company_slice("support_chat", company_id)
    bump_support_conversation(conversation.id)

    owner = getattr(conversation.company, "owner", None)
    if owner and owner.id:
        invalidate_badges(owner.id)

    # Immediate web/mobile push — email unread reminders remain on the cron path.
    try:
        from .notifications import push_new_message_to_agents, push_new_message_to_owner

        if side == SupportMessage.Side.TENANT:
            push_new_message_to_agents(conversation, msg)
        elif side == SupportMessage.Side.SUPPORT:
            push_new_message_to_owner(conversation, msg)
    except Exception:
        logger.warning("Support chat push dispatch failed", exc_info=True)

    return msg


def mark_read(conversation: SupportConversation, side: str, message: SupportMessage) -> None:
    if message.conversation_id != conversation.id:
        raise ValueError("Message does not belong to this conversation.")

    update_fields = []
    if side == SupportMessage.Side.TENANT:
        current_id = conversation.tenant_last_read_message_id or 0
        if message.id > current_id:
            conversation.tenant_last_read_message = message
            update_fields.append("tenant_last_read_message")
    elif side == SupportMessage.Side.SUPPORT:
        current_id = conversation.support_last_read_message_id or 0
        if message.id > current_id:
            conversation.support_last_read_message = message
            update_fields.append("support_last_read_message")
    else:
        raise ValueError("Invalid side for mark_read.")

    if not update_fields:
        return

    conversation.save(update_fields=update_fields + ["updated_at"])
    bump_company_slice("support_chat", conversation.company_id)
    bump_support_conversation(conversation.id)

    owner = getattr(conversation.company, "owner", None)
    if owner and owner.id:
        invalidate_badges(owner.id)


def resolve(conversation: SupportConversation, by) -> SupportConversation:
    with transaction.atomic():
        conversation = SupportConversation.objects.select_for_update().get(pk=conversation.pk)
        conversation.status = SupportConversation.Status.RESOLVED
        conversation.resolved_at = timezone.now()
        conversation.resolved_by = by
        conversation.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])
    bump_support_conversation(conversation.id)
    return conversation


def reopen(conversation: SupportConversation) -> SupportConversation:
    with transaction.atomic():
        conversation = SupportConversation.objects.select_for_update().get(pk=conversation.pk)
        conversation.status = SupportConversation.Status.OPEN
        conversation.resolved_at = None
        conversation.resolved_by = None
        conversation.save(
            update_fields=["status", "resolved_at", "resolved_by", "updated_at"]
        )
    bump_support_conversation(conversation.id)
    return conversation
