import logging

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from accounts.permissions import HasActiveSubscription
from notifications.models import NotificationType
from notifications.services import NotificationService

from .authorization import can_chat, eligible_company_users_queryset, user_can_access_chat_message
from .models import ChatConversation, ChatConversationReadState, ChatMessage, ChatPinnedMessage
from .permissions import IsTenantChatUser
from .serializers import (
    ChatConversationSerializer,
    ChatMessageSerializer,
    ChatPeerSerializer,
    MarkReadSerializer,
    PinMessageSerializer,
    SendMessageSerializer,
    StartConversationSerializer,
    normalize_dm_participants,
)

logger = logging.getLogger(__name__)


class TenantChatConversationViewSet(viewsets.ModelViewSet):
    """
    Internal DM threads scoped to the authenticated user's company.

    list — conversations the user participates in (latest activity first).
    create — body: { \"with_user_id\": <int> } (get or create).
    messages — GET/POST on .../conversations/<id>/messages/
    eligible-users — GET .../conversations/eligible-users/
    """

    permission_classes = [
        IsAuthenticated,
        HasActiveSubscription,
        IsTenantChatUser,
    ]
    serializer_class = ChatConversationSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        pin_qs = ChatPinnedMessage.objects.order_by("-pinned_at").select_related(
            "message", "message__sender", "pinned_by"
        )
        return (
            ChatConversation.objects.filter(
                Q(participant_low=user) | Q(participant_high=user),
                company_id=user.company_id,
            )
            .select_related("participant_low", "participant_high")
            .prefetch_related(Prefetch("chat_pins", queryset=pin_qs, to_attr="_prefetched_chat_pins"))
            .order_by("-updated_at")
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def create(self, request, *args, **kwargs):
        ser = StartConversationSerializer(data=request.data)
        if not ser.is_valid():
            from crm_saas_api.responses import validation_error_response

            return validation_error_response(ser.errors)

        other = get_object_or_404(
            User.objects.filter(company_id=request.user.company_id),
            pk=ser.validated_data["with_user_id"],
        )
        if other.id == request.user.id:
            from crm_saas_api.responses import error_response

            return error_response("Cannot start a chat with yourself.", code="invalid_peer")

        if not can_chat(request.user, other):
            from crm_saas_api.responses import error_response

            return error_response(
                "You are not allowed to chat with this user.",
                code="chat_not_allowed",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        low, high = normalize_dm_participants(request.user, other)
        conv, _created = ChatConversation.objects.get_or_create(
            company_id=request.user.company_id,
            participant_low=low,
            participant_high=high,
        )
        out = ChatConversationSerializer(conv, context=self.get_serializer_context())
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="eligible-users")
    def eligible_users(self, request):
        base = eligible_company_users_queryset(
            User.objects.filter(company_id=request.user.company_id)
        ).exclude(pk=request.user.id)

        peers = []
        for u in base.select_related().iterator():
            if can_chat(request.user, u):
                peers.append(u)

        data = ChatPeerSerializer(peers, many=True, context=self.get_serializer_context()).data
        return Response({"results": data, "count": len(data)})

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        conversation = self.get_object()
        if request.method == "GET":
            order = self.request.query_params.get("ordering") or "-created_at"
            if order not in ("created_at", "-created_at"):
                order = "-created_at"
            qs = (
                ChatMessage.objects.filter(conversation=conversation)
                .select_related(
                    "sender",
                    "reply_to",
                    "reply_to__sender",
                    "forwarded_from",
                    "forwarded_from__sender",
                )
                .order_by(order)
            )
            page = self.paginate_queryset(qs)
            other_id = (
                conversation.participant_high_id
                if conversation.participant_low_id == request.user.id
                else conversation.participant_low_id
            )
            peer_state = ChatConversationReadState.objects.filter(
                conversation=conversation, user_id=other_id
            ).first()
            peer_lr = peer_state.last_read_message_id if peer_state else None
            ctx = {
                **self.get_serializer_context(),
                "conversation": conversation,
                "peer_last_read_message_id": peer_lr,
            }
            to_serialize = page if page is not None else qs
            ser = ChatMessageSerializer(to_serialize, many=True, context=ctx)
            if page is not None:
                return self.get_paginated_response(ser.data)
            return Response(ser.data)

        ser = SendMessageSerializer(data=request.data)
        if not ser.is_valid():
            from crm_saas_api.responses import validation_error_response

            return validation_error_response(ser.errors)

        other_id = (
            conversation.participant_high_id
            if conversation.participant_low_id == request.user.id
            else conversation.participant_low_id
        )
        other = get_object_or_404(User, pk=other_id)
        if not can_chat(request.user, other):
            from crm_saas_api.responses import error_response

            return error_response(
                "You are not allowed to send messages in this conversation.",
                code="chat_not_allowed",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        reply_to = None
        rid = ser.validated_data.get("reply_to_message_id")
        if rid is not None:
            reply_to = get_object_or_404(ChatMessage.objects.filter(conversation=conversation), pk=rid)

        forwarded_from = None
        fid = ser.validated_data.get("forward_from_message_id")
        if fid is not None:
            src = get_object_or_404(
                ChatMessage.objects.filter(
                    pk=fid,
                    conversation__company_id=request.user.company_id,
                )
            )
            if not user_can_access_chat_message(request.user, src):
                from crm_saas_api.responses import error_response

                return error_response(
                    "You cannot forward this message.",
                    code="chat_forward_denied",
                    status_code=status.HTTP_403_FORBIDDEN,
                )
            forwarded_from = src

        msg = ChatMessage.objects.create(
            conversation=conversation,
            sender=request.user,
            body=ser.validated_data["body"],
            reply_to=reply_to,
            forwarded_from=forwarded_from,
        )
        _notify_recipient_chat_message(request.user, other, msg)
        peer_state = ChatConversationReadState.objects.filter(
            conversation=conversation, user_id=other.id
        ).first()
        peer_lr = peer_state.last_read_message_id if peer_state else None
        msg_ctx = {
            **self.get_serializer_context(),
            "conversation": conversation,
            "peer_last_read_message_id": peer_lr,
        }
        out = ChatMessageSerializer(msg, context=msg_ctx)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        conversation = self.get_object()
        ser = MarkReadSerializer(data=request.data)
        if not ser.is_valid():
            from crm_saas_api.responses import validation_error_response

            return validation_error_response(ser.errors)

        msg = get_object_or_404(
            ChatMessage.objects.filter(conversation=conversation),
            pk=ser.validated_data["message_id"],
        )
        state, _ = ChatConversationReadState.objects.get_or_create(
            conversation=conversation,
            user=request.user,
        )
        cur_id = state.last_read_message_id or 0
        if msg.id > cur_id:
            state.last_read_message = msg
            state.save(update_fields=["last_read_message", "updated_at"])
        return Response(
            {
                "last_read_message_id": state.last_read_message_id,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="pin-message")
    def pin_message(self, request, pk=None):
        conversation = self.get_object()
        ser = PinMessageSerializer(data=request.data)
        if not ser.is_valid():
            from crm_saas_api.responses import validation_error_response

            return validation_error_response(ser.errors)

        msg = get_object_or_404(
            ChatMessage.objects.filter(conversation=conversation),
            pk=ser.validated_data["message_id"],
        )
        ChatPinnedMessage.objects.get_or_create(
            conversation=conversation,
            message=msg,
            defaults={"pinned_by": request.user},
        )
        return Response({"ok": True}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="unpin-message")
    def unpin_message(self, request, pk=None):
        conversation = self.get_object()
        ser = PinMessageSerializer(data=request.data)
        if not ser.is_valid():
            from crm_saas_api.responses import validation_error_response

            return validation_error_response(ser.errors)

        deleted, _ = ChatPinnedMessage.objects.filter(
            conversation=conversation,
            message_id=ser.validated_data["message_id"],
        ).delete()
        return Response({"ok": True, "removed": deleted > 0}, status=status.HTTP_200_OK)


def _notify_recipient_chat_message(sender: User, recipient: User, message: ChatMessage):
    """Push + in-app notification (best-effort)."""
    try:
        label = (sender.get_full_name() or sender.username or "").strip() or "Team"
        preview = (message.body or "").strip().replace("\n", " ")
        if getattr(message, "forwarded_from_id", None):
            orig = getattr(message, "forwarded_from", None)
            fwd = ((orig.body or "").strip().replace("\n", " ") if orig else "").strip()
            if preview:
                preview = (f"[↪] {fwd[:80]} • {preview}" if fwd else f"[↪] {preview}")[:200]
            else:
                preview = (f"[↪] {fwd}" if fwd else "[↪]")[:200]
        if len(preview) > 160:
            preview = preview[:157] + "..."

        NotificationService.send_notification(
            recipient,
            NotificationType.GENERAL.value,
            title=label,
            body=preview,
            data={
                "kind": "tenant_chat",
                "conversation_id": message.conversation_id,
                "message_id": message.id,
                "sender_id": sender.id,
            },
            skip_settings_check=True,
        )
    except Exception as e:
        logger.warning("Chat push notification failed: %s", e, exc_info=True)
