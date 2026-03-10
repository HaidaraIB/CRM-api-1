from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from accounts.permissions import HasActiveSubscription
from .models import SupportTicket, SupportTicketAttachment
from .serializers import (
    SupportTicketSerializer,
    SupportTicketListSerializer,
    SupportTicketStatusSerializer,
)


class SupportTicketViewSet(viewsets.ModelViewSet):
    """
    ViewSet for support tickets.
    - Tenant users: list only their tickets, create new ones.
    - Super admin: list all tickets, can update status (PATCH).
    """

    permission_classes = [IsAuthenticated, HasActiveSubscription]
    http_method_names = ["get", "post", "patch", "head", "options"]

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
        self.perform_create(serializer)
        instance = serializer.instance

        for f in files:
            if f and f.size:
                SupportTicketAttachment.objects.create(ticket=instance, file=f)

        headers = self.get_success_headers(serializer.data)
        # Re-fetch to include attachments in response
        instance.refresh_from_db()
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        instance = serializer.save(
            created_by=self.request.user,
            company=self.request.user.company,
        )
        # Send confirmation email in the user's current language (from X-Language header or DB)
        try:
            from accounts.event_emails import send_support_ticket_created_email
            from accounts.utils import get_email_language_for_user

            language = get_email_language_for_user(
                self.request.user, self.request, default="en"
            )
            send_support_ticket_created_email(
                self.request.user, instance, language=language
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to send support ticket created email: %s", e
            )
