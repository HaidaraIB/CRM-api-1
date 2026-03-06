from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import HasActiveSubscription
from .models import SupportTicket
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
        if user.is_super_admin():
            return SupportTicket.objects.all().select_related(
                "company", "created_by"
            )
        return SupportTicket.objects.filter(created_by=user).select_related(
            "company", "created_by"
        )

    def get_serializer_class(self):
        if self.action == "list":
            return SupportTicketListSerializer
        if self.action in ("partial_update", "update"):
            return SupportTicketStatusSerializer
        return SupportTicketSerializer

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
