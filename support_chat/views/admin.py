from __future__ import annotations

import logging

from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from crm_saas_api.responses import error_response, success_response, validation_error_response
from sync.version import normalize_etag, support_conversation_token, support_inbox_token

from ..models import SupportConversation, SupportMessage
from ..permissions import IsSupportAgent
from ..selectors import admin_inbox_queryset, support_unread_count_for_inbox
from ..serializers import (
    MarkSupportReadSerializer,
    SendSupportMessageSerializer,
    SupportConversationSerializer,
    SupportInboxItemSerializer,
    SupportMessageSerializer,
)
from ..services import conversations as chat_services
from ..services.attachments import chat_storage, message_has_stored_attachment

logger = logging.getLogger(__name__)


def _parse_positive_int(raw):
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


class SupportChatAdminUnreadCountView(APIView):
    permission_classes = [IsAuthenticated, IsSupportAgent]

    def get(self, request):
        return success_response({"unread_count": support_unread_count_for_inbox()})


class SupportConversationAdminViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsSupportAgent]
    serializer_class = SupportInboxItemSerializer
    lookup_field = "pk"

    def get_queryset(self):
        search = self.request.query_params.get("search")
        status_filter = self.request.query_params.get("status") or "all"
        return admin_inbox_queryset(search=search, status_filter=status_filter)

    def list(self, request, *args, **kwargs):
        search = request.query_params.get("search") or ""
        status_filter = request.query_params.get("status") or "all"
        variant = f"{search}|{status_filter}"
        token = support_inbox_token(variant=variant)
        if normalize_etag(request.META.get("HTTP_IF_NONE_MATCH", "")) == token:
            resp = HttpResponse(status=304)
            resp["ETag"] = f'"{token}"'
            resp["Cache-Control"] = "no-store"
            return resp

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            ser = self.get_serializer(page, many=True)
            response = self.get_paginated_response(ser.data)
        else:
            ser = self.get_serializer(queryset, many=True)
            response = Response(ser.data)

        if isinstance(response, Response):
            response["ETag"] = f'"{token}"'
            response["Cache-Control"] = "no-store"
        return response

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        ser = SupportConversationSerializer(
            instance,
            context={"request": request, "viewer_side": SupportMessage.Side.SUPPORT},
        )
        return success_response(ser.data)

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        conversation = self.get_object()
        if request.method == "GET":
            return self._messages_get(request, conversation)
        return self._messages_post(request, conversation)

    def _messages_get(self, request, conversation):
        before_id = _parse_positive_int(request.query_params.get("before_id"))
        after_id = _parse_positive_int(request.query_params.get("after_id"))
        page_size = _parse_positive_int(request.query_params.get("page_size")) or 50
        page_size = min(page_size, 200)
        variant = "|".join(str(v) for v in (before_id, after_id, page_size))
        token = support_conversation_token(conversation.id, "support", variant=variant)
        if normalize_etag(request.META.get("HTTP_IF_NONE_MATCH", "")) == token:
            resp = HttpResponse(status=304)
            resp["ETag"] = f'"{token}"'
            resp["Cache-Control"] = "no-store"
            return resp

        qs_base = SupportMessage.objects.filter(conversation=conversation).select_related(
            "reply_to",
            "conversation__company__owner",
            "sender",
        )
        peer_lr = conversation.tenant_last_read_message_id
        ctx = {
            "request": request,
            "viewer_side": SupportMessage.Side.SUPPORT,
            "for_tenant": False,
            "admin_attachments": True,
            "peer_last_read_message_id": peer_lr,
        }

        if before_id is not None and after_id is not None:
            return error_response(
                "Use only one of before_id or after_id.",
                code="invalid_anchor_params",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if before_id is not None:
            older_qs = qs_base.filter(id__lt=before_id).order_by("-id")[:page_size]
            rows = list(reversed(list(older_qs)))
            has_older = qs_base.filter(id__lt=(rows[0].id if rows else before_id)).exists()
            has_newer = qs_base.filter(id__gte=before_id).exists()
        elif after_id is not None:
            rows = list(qs_base.filter(id__gt=after_id).order_by("id")[:page_size])
            has_older = qs_base.filter(id__lte=after_id).exists()
            has_newer = qs_base.filter(id__gt=(rows[-1].id if rows else after_id)).exists()
        else:
            rows = list(qs_base.order_by("-id")[:page_size])
            rows = list(reversed(rows))
            has_older = bool(rows) and qs_base.filter(id__lt=rows[0].id).exists()
            has_newer = False

        ser = SupportMessageSerializer(rows, many=True, context=ctx)
        payload = {
            "results": ser.data,
            "has_older": has_older,
            "has_newer": has_newer,
        }
        response = success_response(payload)
        response["ETag"] = f'"{token}"'
        response["Cache-Control"] = "no-store"
        return response

    def _messages_post(self, request, conversation):
        uploaded_file = request.FILES.get("file")
        ser = SendSupportMessageSerializer(
            data=request.data,
            context={"has_uploaded_file": bool(uploaded_file)},
        )
        if not ser.is_valid():
            return validation_error_response(ser.errors)
        try:
            msg = chat_services.send_message(
                conversation,
                request.user,
                side=SupportMessage.Side.SUPPORT,
                body=ser.validated_data.get("body") or "",
                uploaded_file=uploaded_file,
                reply_to_id=ser.validated_data.get("reply_to_message_id"),
            )
        except ValueError as e:
            return error_response(str(e), code="invalid_message", status_code=status.HTTP_400_BAD_REQUEST)

        msg = SupportMessage.objects.select_related(
            "reply_to", "conversation__company__owner", "sender"
        ).get(pk=msg.pk)
        ctx = {
            "request": request,
            "viewer_side": SupportMessage.Side.SUPPORT,
            "for_tenant": False,
            "admin_attachments": True,
            "peer_last_read_message_id": conversation.tenant_last_read_message_id,
        }
        out = SupportMessageSerializer(msg, context=ctx)
        return success_response(out.data, status_code=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        conversation = self.get_object()
        ser = MarkSupportReadSerializer(data=request.data)
        if not ser.is_valid():
            return validation_error_response(ser.errors)
        msg = get_object_or_404(
            SupportMessage.objects.filter(conversation=conversation),
            pk=ser.validated_data["message_id"],
        )
        try:
            chat_services.mark_read(conversation, SupportMessage.Side.SUPPORT, msg)
        except ValueError as e:
            return error_response(str(e), code="invalid_mark_read")
        return success_response({"message_id": msg.id})

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        conversation = self.get_object()
        try:
            chat_services.approve(conversation, request.user)
        except ValueError as e:
            return error_response(str(e), code="invalid_approve")
        return success_response({"status": SupportConversation.Status.OPEN})

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        conversation = self.get_object()
        try:
            chat_services.resolve(conversation, request.user)
        except ValueError as e:
            return error_response(str(e), code="invalid_resolve")
        return success_response({"status": SupportConversation.Status.RESOLVED})

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        conversation = self.get_object()
        chat_services.reopen(conversation)
        return success_response({"status": SupportConversation.Status.OPEN})


class SupportChatAdminMessageAttachmentView(APIView):
    permission_classes = [IsAuthenticated, IsSupportAgent]

    def get(self, request, pk):
        msg = get_object_or_404(SupportMessage.objects.select_related("conversation"), pk=pk)
        if not message_has_stored_attachment(msg):
            return error_response(
                "No attachment.",
                code="no_attachment",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        key = (msg.attachment_object_key or "").strip()
        if key and chat_storage.is_supabase_chat_storage():
            return HttpResponseRedirect(chat_storage.create_signed_url(key))
        if msg.attachment:
            return FileResponse(
                msg.attachment.open("rb"),
                content_type=msg.attachment_mime or "application/octet-stream",
            )
        return error_response(
            "Attachment unavailable.",
            code="attachment_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        )
