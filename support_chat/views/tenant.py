from __future__ import annotations

import logging

from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.two_factor_policy import is_company_owner
from crm_saas_api.responses import error_response, success_response, validation_error_response
from sync.version import normalize_etag, support_conversation_token

from ..models import SupportConversation, SupportMessage
from ..permissions import IsCompanyOwner
from ..serializers import (
    MarkSupportReadSerializer,
    SendSupportMessageSerializer,
    SupportConversationSerializer,
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


class SupportChatConversationView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def get(self, request):
        company = request.user.company
        if not company:
            return error_response(
                "No company associated with this account.",
                code="no_company",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        conv, _created = chat_services.get_or_create_for_company(company)
        conv = SupportConversation.objects.select_related("company").get(pk=conv.pk)
        ser = SupportConversationSerializer(
            conv,
            context={
                "request": request,
                "viewer_side": SupportMessage.Side.TENANT,
            },
        )
        return success_response(ser.data)


class SupportChatMessagesView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def _conversation(self, user) -> SupportConversation:
        company = user.company
        conv, _ = chat_services.get_or_create_for_company(company)
        return SupportConversation.objects.select_related("company").get(pk=conv.pk)

    def get(self, request):
        conversation = self._conversation(request.user)
        order = request.query_params.get("ordering") or "created_at"
        if order not in ("created_at", "-created_at"):
            order = "created_at"
        before_id = _parse_positive_int(request.query_params.get("before_id"))
        after_id = _parse_positive_int(request.query_params.get("after_id"))
        page_size = _parse_positive_int(request.query_params.get("page_size")) or 50
        page_size = min(page_size, 200)

        variant = "|".join(
            str(v)
            for v in (order, before_id, after_id, page_size)
        )
        token = support_conversation_token(
            conversation.id, f"tenant:{request.user.id}", variant=variant
        )
        if normalize_etag(request.META.get("HTTP_IF_NONE_MATCH", "")) == token:
            resp = HttpResponse(status=304)
            resp["ETag"] = f'"{token}"'
            resp["Cache-Control"] = "no-store"
            return resp

        qs_base = SupportMessage.objects.filter(conversation=conversation).select_related(
            "reply_to",
            "conversation__company__owner",
        )
        peer_lr = conversation.support_last_read_message_id
        ctx = {
            "request": request,
            "viewer_side": SupportMessage.Side.TENANT,
            "for_tenant": True,
            "admin_attachments": False,
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
            has_older = qs_base.filter(id__lt=(rows[0].id if rows else 0)).exists() if rows else False
            has_newer = False

        ser = SupportMessageSerializer(rows, many=True, context=ctx)
        payload = {
            "results": ser.data,
            "has_older": has_older,
            "has_newer": has_newer,
            "conversation": SupportConversationSerializer(
                conversation,
                context={"request": request, "viewer_side": SupportMessage.Side.TENANT},
            ).data,
        }
        response = success_response(payload)
        response["ETag"] = f'"{token}"'
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        if not is_company_owner(request.user):
            return error_response(
                "Only the company owner can send support messages.",
                code="forbidden",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        conversation = self._conversation(request.user)
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
                side=SupportMessage.Side.TENANT,
                body=ser.validated_data.get("body") or "",
                uploaded_file=uploaded_file,
                reply_to_id=ser.validated_data.get("reply_to_message_id"),
            )
        except ValueError as e:
            err_text = str(e)
            code = "invalid_message"
            if "exceeds" in err_text.lower() or "file" in err_text.lower():
                code = "invalid_file_type"
            return error_response(err_text, code=code, status_code=status.HTTP_400_BAD_REQUEST)

        msg = SupportMessage.objects.select_related(
            "reply_to", "conversation__company__owner"
        ).get(pk=msg.pk)
        ctx = {
            "request": request,
            "viewer_side": SupportMessage.Side.TENANT,
            "for_tenant": True,
            "admin_attachments": False,
            "peer_last_read_message_id": conversation.support_last_read_message_id,
        }
        out = SupportMessageSerializer(msg, context=ctx)
        return success_response(out.data, status_code=status.HTTP_201_CREATED)


class SupportChatMarkReadView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def post(self, request):
        company = request.user.company
        conv, _ = chat_services.get_or_create_for_company(company)
        ser = MarkSupportReadSerializer(data=request.data)
        if not ser.is_valid():
            return validation_error_response(ser.errors)
        msg = get_object_or_404(
            SupportMessage.objects.filter(conversation=conv),
            pk=ser.validated_data["message_id"],
        )
        try:
            chat_services.mark_read(conv, SupportMessage.Side.TENANT, msg)
        except ValueError as e:
            return error_response(str(e), code="invalid_mark_read")
        return success_response({"message_id": msg.id})


class SupportChatMessageAttachmentView(APIView):
    permission_classes = [IsAuthenticated, IsCompanyOwner]

    def get(self, request, pk):
        msg = get_object_or_404(
            SupportMessage.objects.select_related("conversation__company"),
            pk=pk,
        )
        if msg.conversation.company_id != request.user.company_id:
            return error_response(
                "Not found.",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not message_has_stored_attachment(msg):
            return error_response(
                "No attachment.",
                code="no_attachment",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        key = (msg.attachment_object_key or "").strip()
        if key and chat_storage.is_supabase_chat_storage():
            url = chat_storage.create_signed_url(key)
            return HttpResponseRedirect(url)
        if msg.attachment:
            return FileResponse(msg.attachment.open("rb"), content_type=msg.attachment_mime or "application/octet-stream")
        return error_response(
            "Attachment unavailable.",
            code="attachment_unavailable",
            status_code=status.HTTP_404_NOT_FOUND,
        )
