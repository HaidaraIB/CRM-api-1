from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from accounts.models import User
from .attachments import media_preview_label
from .models import ChatConversation, ChatConversationReadState, ChatMessage, ChatPinnedMessage
from .presence import VALID_ACTIONS


def _body_snippet(text: str | None, n: int = 200) -> str:
    s = (text or "").strip()
    if len(s) > n:
        return s[: n - 1] + "…"
    return s


def _message_list_snippet(msg: ChatMessage) -> str:
    if getattr(msg, "attachment_kind", None):
        return media_preview_label(msg.attachment_kind, msg.body)
    return _body_snippet(msg.body)


def _attachment_download_url(request, message_id: int) -> str | None:
    if not request:
        return None
    path = reverse("tenant_chat_message_attachment", kwargs={"pk": message_id})
    return request.build_absolute_uri(path)


def _message_has_attachment_payload(m: ChatMessage | None) -> bool:
    if m is None:
        return False
    if (getattr(m, "attachment_object_key", None) or "").strip():
        return True
    att = getattr(m, "attachment", None)
    return bool(att and getattr(att, "name", None))


class ChatPeerSerializer(serializers.ModelSerializer):
    """Minimal user info shown in chat lists."""

    profile_photo = serializers.ImageField(read_only=True)
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "profile_photo",
            "last_seen_at",
            "last_seen_source",
            "is_online",
        )
        read_only_fields = fields

    def get_is_online(self, obj):
        if not obj.last_seen_at:
            return False
        return (timezone.now() - obj.last_seen_at) <= timedelta(seconds=90)


class ChatMessageSerializer(serializers.ModelSerializer):
    sender = ChatPeerSerializer(read_only=True)
    read_by_peer = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()
    forwarded_from = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "sender",
            "body",
            "created_at",
            "read_by_peer",
            "reply_to",
            "forwarded_from",
            "attachment_kind",
            "attachment_mime",
            "attachment_size",
            "original_filename",
            "attachment_url",
        )
        read_only_fields = fields

    def get_read_by_peer(self, obj):
        request = self.context.get("request")
        peer_lr = self.context.get("peer_last_read_message_id")
        if not request or not request.user.is_authenticated:
            return False
        if obj.sender_id != request.user.id:
            return False
        if peer_lr is None:
            return False
        return peer_lr >= obj.id

    def get_attachment_url(self, obj):
        if not _message_has_attachment_payload(obj):
            return None
        return _attachment_download_url(self.context.get("request"), obj.id)

    def _message_quote(self, m: ChatMessage | None):
        if m is None:
            return None
        out = {
            "id": m.id,
            "sender": ChatPeerSerializer(m.sender, context=self.context).data,
            "body": _body_snippet(m.body),
            "created_at": m.created_at,
            "attachment_kind": m.attachment_kind,
            "attachment_url": _attachment_download_url(self.context.get("request"), m.id)
            if _message_has_attachment_payload(m)
            else None,
        }
        return out

    def get_reply_to(self, obj):
        return self._message_quote(obj.reply_to)

    def get_forwarded_from(self, obj):
        return self._message_quote(obj.forwarded_from)


class ChatConversationSerializer(serializers.ModelSerializer):
    other_user = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    last_read_message_id = serializers.SerializerMethodField()
    pinned_messages = serializers.SerializerMethodField()

    class Meta:
        model = ChatConversation
        fields = (
            "id",
            "other_user",
            "last_message",
            "updated_at",
            "unread_count",
            "last_read_message_id",
            "pinned_messages",
        )
        read_only_fields = fields

    def get_other_user(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        other = (
            obj.participant_high
            if obj.participant_low_id == request.user.id
            else obj.participant_low
        )
        return ChatPeerSerializer(other, context=self.context).data

    def get_last_message(self, obj):
        msg = getattr(obj, "last_message_prefetched", None)
        if msg is None:
            msg = obj.messages.order_by("-created_at").first()
        if not msg:
            return None
        preview = _message_list_snippet(msg)
        return {
            "id": msg.id,
            "body": preview[:500],
            "created_at": msg.created_at,
            "sender_id": msg.sender_id,
            "attachment_kind": msg.attachment_kind or None,
        }

    def get_unread_count(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return 0
        user = request.user
        state = ChatConversationReadState.objects.filter(conversation=obj, user=user).first()
        last_id = state.last_read_message_id if state else None
        qs = ChatMessage.objects.filter(conversation=obj).exclude(sender=user)
        if last_id:
            qs = qs.filter(id__gt=last_id)
        return qs.count()

    def get_last_read_message_id(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        state = ChatConversationReadState.objects.filter(conversation=obj, user=request.user).first()
        return state.last_read_message_id if state else None

    def get_pinned_messages(self, obj):
        pins = getattr(obj, "_prefetched_chat_pins", None)
        if pins is None:
            pins = (
                ChatPinnedMessage.objects.filter(conversation=obj)
                .select_related("message", "message__sender", "pinned_by")
                .order_by("-pinned_at")[:30]
            )
        out = []
        for pin in pins:
            m = pin.message
            out.append(
                {
                    "pin_id": pin.id,
                    "message_id": m.id,
                    "body": _message_list_snippet(m),
                    "sender": ChatPeerSerializer(m.sender, context=self.context).data,
                    "pinned_at": pin.pinned_at,
                    "pinned_by_id": pin.pinned_by_id,
                    "attachment_kind": m.attachment_kind or None,
                }
            )
        return out


class StartConversationSerializer(serializers.Serializer):
    with_user_id = serializers.IntegerField(min_value=1)


class SendMessageSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=8000, required=False, allow_blank=True, default="")
    reply_to_message_id = serializers.IntegerField(required=False, allow_null=True)
    forward_from_message_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        body_raw = attrs.get("body", "")
        body = body_raw.strip() if isinstance(body_raw, str) else ""
        rid = attrs.get("reply_to_message_id")
        fid = attrs.get("forward_from_message_id")
        has_file = bool(self.context.get("has_uploaded_file"))

        if rid is not None and fid is not None:
            raise serializers.ValidationError("Cannot combine reply_to_message_id with forward_from_message_id.")

        attrs["reply_to_message_id"] = rid
        attrs["forward_from_message_id"] = fid

        if fid is not None:
            attrs["body"] = body
            return attrs

        if rid is not None and not body and not has_file:
            raise serializers.ValidationError({"body": "Message body cannot be empty."})
        if rid is None and fid is None and not body and not has_file:
            raise serializers.ValidationError({"body": "Message body cannot be empty."})
        attrs["body"] = body
        return attrs


class MarkReadSerializer(serializers.Serializer):
    message_id = serializers.IntegerField(min_value=1)


class PinMessageSerializer(serializers.Serializer):
    message_id = serializers.IntegerField(min_value=1)


class ChatPresenceSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=[(a, a) for a in sorted(VALID_ACTIONS)])


def normalize_dm_participants(user_a: User, user_b: User):
    """Return (low_user, high_user) with strictly increasing ids."""
    if user_a.id < user_b.id:
        return user_a, user_b
    return user_b, user_a
