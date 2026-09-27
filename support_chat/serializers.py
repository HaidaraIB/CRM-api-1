from __future__ import annotations

from django.urls import reverse
from rest_framework import serializers

from .models import SupportConversation, SupportMessage
from .services.attachments import message_has_stored_attachment

LOOP_SUPPORT_DISPLAY = "LOOP Support"


def _attachment_url(request, message_id: int, admin: bool) -> str | None:
    if not request:
        return None
    name = (
        "support_chat_admin_message_attachment"
        if admin
        else "support_chat_message_attachment"
    )
    path = reverse(name, kwargs={"pk": message_id})
    return request.build_absolute_uri(path)


class SendSupportMessageSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=8000, required=False, allow_blank=True)
    reply_to_message_id = serializers.IntegerField(required=False, allow_null=True)


class MarkSupportReadSerializer(serializers.Serializer):
    message_id = serializers.IntegerField(min_value=1)


class SupportConversationSerializer(serializers.ModelSerializer):
    awaiting_reply = serializers.BooleanField(read_only=True)
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = SupportConversation
        fields = (
            "id",
            "status",
            "last_message_at",
            "last_message_side",
            "last_message_preview",
            "awaiting_reply",
            "unread_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_unread_count(self, obj):
        viewer = self.context.get("viewer_side")
        if viewer == SupportMessage.Side.SUPPORT:
            cursor = obj.support_last_read_message_id or 0
            side = SupportMessage.Side.TENANT
        else:
            cursor = obj.tenant_last_read_message_id or 0
            side = SupportMessage.Side.SUPPORT
        return obj.messages.filter(side=side, id__gt=cursor).count()


class SupportMessageSerializer(serializers.ModelSerializer):
    is_mine = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    read_by_peer = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    sender = serializers.SerializerMethodField()

    class Meta:
        model = SupportMessage
        fields = (
            "id",
            "side",
            "body",
            "created_at",
            "is_mine",
            "display_name",
            "read_by_peer",
            "reply_to",
            "attachment_kind",
            "attachment_mime",
            "attachment_size",
            "attachment_width",
            "attachment_height",
            "original_filename",
            "attachment_url",
            "sender",
        )
        read_only_fields = fields

    def _viewer_side(self):
        return self.context.get("viewer_side")

    def _for_tenant(self) -> bool:
        return self.context.get("for_tenant", False)

    def _admin_attachments(self) -> bool:
        return self.context.get("admin_attachments", False)

    def get_is_mine(self, obj):
        side = self._viewer_side()
        if side == SupportMessage.Side.TENANT:
            return obj.side == SupportMessage.Side.TENANT
        return obj.side == SupportMessage.Side.SUPPORT

    def get_display_name(self, obj):
        if obj.side == SupportMessage.Side.SUPPORT:
            return LOOP_SUPPORT_DISPLAY
        if self._for_tenant():
            return None
        owner = getattr(obj.conversation.company, "owner", None)
        if owner:
            return owner.get_full_name().strip() or owner.email
        return "Owner"

    def get_sender(self, obj):
        if self._for_tenant():
            return None
        if obj.side == SupportMessage.Side.SUPPORT:
            return {"id": obj.sender_id, "label": LOOP_SUPPORT_DISPLAY}
        owner = obj.conversation.company.owner
        return {
            "id": owner.id,
            "label": owner.get_full_name().strip() or owner.email,
        }

    def get_read_by_peer(self, obj):
        peer_lr = self.context.get("peer_last_read_message_id")
        side = self._viewer_side()
        if side == SupportMessage.Side.TENANT:
            if obj.side != SupportMessage.Side.TENANT:
                return False
        else:
            if obj.side != SupportMessage.Side.SUPPORT:
                return False
        if peer_lr is None:
            return False
        return peer_lr >= obj.id

    def get_attachment_url(self, obj):
        if not message_has_stored_attachment(obj):
            return None
        return _attachment_url(
            self.context.get("request"),
            obj.id,
            admin=self._admin_attachments(),
        )

    def _quote(self, m: SupportMessage | None):
        if m is None:
            return None
        return SupportMessageSerializer(
            m,
            context={**self.context, "nested_quote": True},
        ).data

    def get_reply_to(self, obj):
        if obj.reply_to_id is None:
            return None
        reply = obj.reply_to
        if reply is None:
            return None
        body = (reply.body or "").strip()
        if len(body) > 200:
            body = body[:199] + "…"
        name = LOOP_SUPPORT_DISPLAY if reply.side == SupportMessage.Side.SUPPORT else None
        if not name and not self._for_tenant():
            owner = reply.conversation.company.owner
            name = owner.get_full_name().strip() or owner.email
        return {
            "id": reply.id,
            "side": reply.side,
            "display_name": name or LOOP_SUPPORT_DISPLAY if reply.side == SupportMessage.Side.SUPPORT else "You",
            "body": body,
            "attachment_kind": reply.attachment_kind,
        }


class SupportInboxItemSerializer(serializers.ModelSerializer):
    company_id = serializers.IntegerField(source="company.id", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    company_domain = serializers.CharField(source="company.domain", read_only=True)
    owner_name = serializers.SerializerMethodField()
    owner_email = serializers.EmailField(source="company.owner.email", read_only=True)
    awaiting_reply = serializers.BooleanField(read_only=True)
    support_unread_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = SupportConversation
        fields = (
            "id",
            "company_id",
            "company_name",
            "company_domain",
            "owner_name",
            "owner_email",
            "status",
            "last_message_at",
            "last_message_side",
            "last_message_preview",
            "awaiting_reply",
            "support_unread_count",
            "updated_at",
        )
        read_only_fields = fields

    def get_owner_name(self, obj):
        owner = obj.company.owner
        if not owner:
            return ""
        return owner.get_full_name().strip() or owner.username
