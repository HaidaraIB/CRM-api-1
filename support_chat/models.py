from __future__ import annotations

from django.conf import settings
from django.db import models

from companies.models import Company


class AttachmentFieldsMixin(models.Model):
    class AttachmentKind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        DOCUMENT = "document", "Document"

    attachment = models.FileField(
        upload_to="support_chat/%Y/%m/%d/",
        max_length=500,
        null=True,
        blank=True,
    )
    attachment_kind = models.CharField(
        max_length=16,
        choices=AttachmentKind.choices,
        null=True,
        blank=True,
    )
    attachment_mime = models.CharField(max_length=128, blank=True, default="")
    attachment_size = models.PositiveIntegerField(null=True, blank=True)
    attachment_width = models.PositiveIntegerField(null=True, blank=True)
    attachment_height = models.PositiveIntegerField(null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    attachment_object_key = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        abstract = True


class SupportConversation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    class LastMessageSide(models.TextChoices):
        TENANT = "tenant", "Tenant"
        SUPPORT = "support", "Support"

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="support_conversation",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RESOLVED,
        db_index=True,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_side = models.CharField(
        max_length=16,
        choices=LastMessageSide.choices,
        null=True,
        blank=True,
    )
    last_message_preview = models.CharField(max_length=200, blank=True, default="")
    tenant_last_read_message = models.ForeignKey(
        "SupportMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    support_last_read_message = models.ForeignKey(
        "SupportMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    tenant_emailed_up_to_id = models.PositiveBigIntegerField(default=0)
    support_emailed_up_to_id = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_chat_conversations"
        indexes = [
            models.Index(fields=["status", "-last_message_at"]),
        ]

    def __str__(self):
        return f"SupportChat company={self.company_id}"

    @property
    def awaiting_reply(self) -> bool:
        if self.status == self.Status.PENDING:
            return True
        return (
            self.status == self.Status.OPEN
            and self.last_message_side == self.LastMessageSide.TENANT
        )


class SupportMessage(AttachmentFieldsMixin, models.Model):
    class Side(models.TextChoices):
        TENANT = "tenant", "Tenant"
        SUPPORT = "support", "Support"
        SYSTEM = "system", "System"

    conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_chat_messages_sent",
    )
    side = models.CharField(max_length=16, choices=Side.choices, db_index=True)
    body = models.TextField(max_length=8000, blank=True, default="")
    reply_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_chat_messages"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self):
        return f"SupportMsg {self.id} conv={self.conversation_id}"
