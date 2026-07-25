import logging
import threading
from datetime import timedelta

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from crm_saas_api.responses import error_response, success_response
from accounts.permissions import HasActiveSubscription
from .models import SupportTicket, SupportTicketAttachment, TicketStatus
from .serializers import (
    SupportTicketSerializer,
    SupportTicketListSerializer,
    SupportTicketStatusSerializer,
)

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per file
DUPLICATE_WINDOW = timedelta(minutes=2)


def _send_support_ticket_emails_async(user_id, ticket_id, language):
    """Fire-and-forget emails so create HTTP responses are not blocked by SMTP."""

    def _run():
        try:
            from accounts.models import User
            from accounts.event_emails import (
                send_support_ticket_created_email,
                send_support_ticket_new_admin_notifications,
            )

            user = User.objects.get(pk=user_id)
            ticket = SupportTicket.objects.select_related(
                "company", "created_by"
            ).get(pk=ticket_id)
            try:
                send_support_ticket_created_email(
                    user, ticket, language=language
                )
            except Exception as e:
                logger.exception(
                    "Failed to send support ticket created email: %s", e
                )
            try:
                send_support_ticket_new_admin_notifications(user, ticket)
            except Exception as e:
                logger.exception(
                    "Failed to send super-admin support ticket notification: %s",
                    e,
                )
        except Exception as e:
            logger.exception(
                "Failed to send support ticket emails (async): %s", e
            )

    threading.Thread(target=_run, daemon=True).start()


class SupportTicketViewSet(viewsets.ModelViewSet):
    """
    ViewSet for support tickets.
    - Tenant users: list only their tickets, create new ones.
    - Super admin: list all tickets, can update status (PATCH) and delete.
    """

    permission_classes = [IsAuthenticated, HasActiveSubscription]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = SupportTicket.objects.all()
        if not user.is_super_admin():
            qs = qs.filter(created_by=user)
        return qs.select_related("company", "created_by").prefetch_related(
            "attachments"
        )

    def get_serializer_class(self):
        if self.action == "list":
            return SupportTicketListSerializer
        if self.action in ("partial_update", "update"):
            return SupportTicketStatusSerializer
        return SupportTicketSerializer

    def _find_recent_duplicate(self, title, description):
        """Same user + title + description within DUPLICATE_WINDOW (open only)."""
        cutoff = timezone.now() - DUPLICATE_WINDOW
        return (
            SupportTicket.objects.filter(
                created_by=self.request.user,
                title=title,
                description=description,
                status=TicketStatus.OPEN,
                created_at__gte=cutoff,
            )
            .order_by("-created_at")
            .first()
        )

    def create(self, request, *args, **kwargs):
        """Accept JSON or multipart/form-data with optional screenshots (multiple)."""
        is_multipart = "multipart/form-data" in (request.content_type or "")
        if is_multipart:
            data = request.data
            files = request.FILES.getlist("screenshots")
        else:
            data = request.data
            files = []

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        title = serializer.validated_data["title"]
        description = serializer.validated_data["description"]
        existing = self._find_recent_duplicate(title, description)
        if existing is not None:
            # Return existing ticket so retries/multi-taps do not flood DB/email.
            # 201 keeps mobile/web clients that expect create success codes happy.
            out = self.get_serializer(existing)
            headers = self.get_success_headers(out.data)
            return success_response(
                data=out.data,
                status_code=status.HTTP_201_CREATED,
                headers=headers,
            )

        self.perform_create(serializer)
        instance = serializer.instance

        for f in files:
            if not f or not f.size:
                continue
            if f.content_type not in ALLOWED_IMAGE_TYPES:
                return error_response(
                    f"File type '{f.content_type}' is not allowed. Use JPEG, PNG, GIF, or WebP.",
                    code="invalid_file_type",
                )
            if f.size > MAX_FILE_SIZE:
                return error_response(
                    f"File '{f.name}' exceeds the 5 MB limit.",
                    code="file_too_large",
                )
            SupportTicketAttachment.objects.create(ticket=instance, file=f)

        headers = self.get_success_headers(serializer.data)
        # Re-fetch to include attachments in response
        instance.refresh_from_db()
        serializer = self.get_serializer(instance)
        return success_response(
            data=serializer.data,
            status_code=status.HTTP_201_CREATED,
            headers=headers,
        )

    def perform_create(self, serializer):
        instance = serializer.save(
            created_by=self.request.user,
            company=self.request.user.company,
        )
        from accounts.utils import get_email_language_for_user

        language = get_email_language_for_user(
            self.request.user, self.request, default="en"
        )
        _send_support_ticket_emails_async(
            self.request.user.pk, instance.pk, language
        )

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_super_admin():
            return error_response(
                "Only super admins can delete support tickets.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        instance = self.get_object()
        instance.delete()
        return success_response(status_code=status.HTTP_204_NO_CONTENT)
