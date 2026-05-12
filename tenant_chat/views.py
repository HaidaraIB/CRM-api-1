import logging

from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import error_response, validation_error_response
from notifications.models import NotificationType
from notifications.services import NotificationService

from . import supabase_storage as chat_storage
from .attachments import (
    KIND_IMAGE,
    copy_chat_attachment_from_source,
    image_upload_pixel_dimensions,
    media_preview_label,
    message_has_stored_attachment,
    normalize_chat_image_upload,
    safe_original_filename,
    validate_uploaded_file,
)
from .authorization import (
    can_chat,
    eligible_company_users_queryset,
    user_can_access_chat_message,
    user_participates_in_conversation,
)
from .models import ChatConversation, ChatConversationReadState, ChatMessage, ChatPinnedMessage
from .permissions import IsTenantChatUser
from .presence import get_user_presence, other_participant_id, set_user_presence
from .serializers import (
    ChatConversationSerializer,
    ChatMessageSerializer,
    ChatPeerSerializer,
    ChatPresenceSerializer,
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
            return error_response("Cannot start a chat with yourself.", code="invalid_peer")

        if not can_chat(request.user, other):
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

    @action(detail=True, methods=["get", "post"], url_path="peer-presence")
    def peer_presence(self, request, pk=None):
        """
        GET — other participant's ephemeral activity (typing, uploading media, recording voice, sending).
        POST — set own activity (heartbeat every few seconds while active; action \"idle\" clears).
        """
        conversation = self.get_object()
        if request.method == "GET":
            peer_id = other_participant_id(conversation, request.user.id)
            activity = get_user_presence(conversation.id, peer_id)
            return Response({"peer_user_id": peer_id, "activity": activity})

        ser = ChatPresenceSerializer(data=request.data)
        if not ser.is_valid():
            return validation_error_response(ser.errors)
        action = ser.validated_data["action"]
        set_user_presence(conversation.id, request.user.id, action)
        return Response({"ok": True, "action": action})

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

        uploaded_file = request.FILES.get("file")
        if uploaded_file and request.data.get("forward_from_message_id"):
            return error_response(
                "Cannot upload a file while forwarding a message.",
                code="chat_forward_with_file",
            )

        ser = SendMessageSerializer(data=request.data, context={"has_uploaded_file": bool(uploaded_file)})
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
                return error_response(
                    "You cannot forward this message.",
                    code="chat_forward_denied",
                    status_code=status.HTTP_403_FORBIDDEN,
                )
            forwarded_from = src

        if chat_storage.is_supabase_mode_requested() and not chat_storage.is_configured():
            if uploaded_file or (
                forwarded_from and (getattr(forwarded_from, "attachment_object_key", None) or "").strip()
            ):
                return error_response(
                    "Team chat storage is set to Supabase but required settings are missing.",
                    code="supabase_storage_misconfigured",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        body = ser.validated_data["body"]
        processed = None
        mime_final = ""
        size_final = 0
        kind = None
        name_hint = ""
        attachment_width = None
        attachment_height = None
        if uploaded_file:
            try:
                kind, ct, size = validate_uploaded_file(uploaded_file)
            except ValueError as e:
                err_text = str(e)
                code = "file_too_large" if "exceeds" in err_text.lower() else "invalid_file_type"
                return error_response(err_text, code=code)
            mime_final = ct
            size_final = size
            if kind == KIND_IMAGE:
                try:
                    normalized = normalize_chat_image_upload(uploaded_file)
                    if normalized:
                        processed, mime_final, size_final, attachment_width, attachment_height = normalized
                    else:
                        dims = image_upload_pixel_dimensions(uploaded_file)
                        if dims:
                            attachment_width, attachment_height = dims
                except ValueError as e:
                    return error_response(str(e), code="invalid_file_type")
            name_hint = (
                processed.name if processed is not None else safe_original_filename(uploaded_file.name)
            )

        try:
            with transaction.atomic():
                if uploaded_file:
                    msg = ChatMessage.objects.create(
                        conversation=conversation,
                        sender=request.user,
                        body=body,
                        reply_to=reply_to,
                        forwarded_from=forwarded_from,
                        attachment_kind=kind,
                        attachment_mime=mime_final,
                        attachment_size=size_final,
                        attachment_width=attachment_width,
                        attachment_height=attachment_height,
                        original_filename=safe_original_filename(uploaded_file.name),
                    )
                    if chat_storage.is_supabase_chat_storage():
                        key = chat_storage.build_object_key(
                            conversation.company_id, msg.id, name_hint
                        )
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
                    msg = ChatMessage.objects.create(
                        conversation=conversation,
                        sender=request.user,
                        body=body,
                        reply_to=reply_to,
                        forwarded_from=forwarded_from,
                    )

                if forwarded_from and message_has_stored_attachment(forwarded_from):
                    copy_chat_attachment_from_source(forwarded_from, msg)
        except Exception:
            logger.exception("tenant chat message / attachment transaction failed")
            return error_response(
                "Could not save the message or attachment.",
                code="chat_message_save_failed",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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


class TenantChatMessageAttachmentView(APIView):
    """Authenticated download for a chat message attachment (not a public /media URL)."""

    permission_classes = [
        IsAuthenticated,
        HasActiveSubscription,
        IsTenantChatUser,
    ]

    def get(self, request, pk):
        msg = get_object_or_404(ChatMessage.objects.select_related("conversation"), pk=pk)
        if not user_participates_in_conversation(request.user, msg.conversation):
            return Response(status=status.HTTP_403_FORBIDDEN)

        key = (getattr(msg, "attachment_object_key", None) or "").strip()
        if key:
            if not chat_storage.is_supabase_chat_storage():
                return Response(status=status.HTTP_404_NOT_FOUND)
            try:
                signed = chat_storage.create_signed_url(key)
            except Exception:
                logger.exception("tenant chat signed URL failed for message %s", pk)
                return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
            return HttpResponseRedirect(signed)

        if not msg.attachment:
            return Response(status=status.HTTP_404_NOT_FOUND)

        filename = msg.original_filename or "attachment"
        stream = msg.attachment.open("rb")
        resp = FileResponse(
            stream,
            as_attachment=False,
            filename=filename,
            content_type=msg.attachment_mime or "application/octet-stream",
        )
        resp["Cache-Control"] = "private, max-age=3600"
        return resp


def _notify_recipient_chat_message(sender: User, recipient: User, message: ChatMessage):
    """FCM push only (no in-app Notification row; team chat has its own unread UI)."""
    try:
        label = (sender.get_full_name() or sender.username or "").strip() or "Team"
        if getattr(message, "attachment_kind", None):
            preview = media_preview_label(message.attachment_kind, message.body)
        else:
            preview = (message.body or "").strip().replace("\n", " ")
        if getattr(message, "forwarded_from_id", None):
            orig = getattr(message, "forwarded_from", None)
            if orig and getattr(orig, "attachment_kind", None):
                fwd = media_preview_label(orig.attachment_kind, orig.body).strip()
            else:
                fwd = ((orig.body or "").strip().replace("\n", " ") if orig else "").strip()
            if preview:
                preview = (f"[↪] {fwd[:80]} • {preview}" if fwd else f"[↪] {preview}")[:200]
            else:
                preview = (f"[↪] {fwd}" if fwd else "[↪]")[:200]
        if len(preview) > 160:
            preview = preview[:157] + "..."

        # Push only: do not create Notification rows (team chat has its own UI + unread).
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
            skip_database_insert=True,
        )
    except Exception as e:
        logger.warning("Chat push notification failed: %s", e, exc_info=True)
