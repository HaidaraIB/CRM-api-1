from django.conf import settings
from django.db import models
from django.db.models import F, Q

from companies.models import Company


class ChatConversation(models.Model):
    """
    One row per unordered pair of participants within a company.
    participant_low_id is always strictly less than participant_high_id.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="chat_conversations",
    )
    participant_low = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    participant_high = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_chat_conversations"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "participant_low", "participant_high"],
                name="uniq_tenant_chat_pair_per_company",
            ),
            models.CheckConstraint(
                check=Q(participant_low_id__lt=F("participant_high_id")),
                name="tenant_chat_low_lt_high",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "updated_at"]),
        ]

    def __str__(self):
        return f"Chat {self.company_id}: {self.participant_low_id}-{self.participant_high_id}"


class ChatMessage(models.Model):
    class AttachmentKind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        DOCUMENT = "document", "Document"

    conversation = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_chat_messages",
    )
    body = models.TextField(max_length=8000)
    reply_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    forwarded_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="forwards",
    )
    attachment = models.FileField(
        upload_to="tenant_chat/%Y/%m/%d/",
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
    original_filename = models.CharField(max_length=255, blank=True, default="")
    attachment_object_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Supabase Storage object path when TENANT_CHAT_STORAGE=supabase (empty for local FileField).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_chat_messages"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self):
        return f"Msg {self.id} conv={self.conversation_id}"


class ChatConversationReadState(models.Model):
    """
    Per-user read cursor for a conversation (DM).
    Used for unread counts and read receipts on outgoing messages.
    """

    conversation = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name="read_states",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    last_read_message = models.ForeignKey(
        ChatMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_chat_read_states"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "user"],
                name="uniq_tenant_chat_read_per_user",
            ),
        ]

    def __str__(self):
        return f"ReadState conv={self.conversation_id} user={self.user_id}"


class ChatPinnedMessage(models.Model):
    """Pinned message in a DM thread (shown at top)."""

    conversation = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name="chat_pins",
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="pins",
    )
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    pinned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_chat_pinned_messages"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "message"],
                name="uniq_tenant_chat_pin_per_message",
            ),
        ]
        ordering = ["-pinned_at"]

    def __str__(self):
        return f"Pin conv={self.conversation_id} msg={self.message_id}"
