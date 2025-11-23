from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from .models import Plan, Subscription, Payment, Invoice, Broadcast, PaymentGateway
from .serializers import (
    PlanSerializer,
    PlanListSerializer,
    SubscriptionSerializer,
    SubscriptionListSerializer,
    PaymentSerializer,
    PaymentListSerializer,
    InvoiceSerializer,
    InvoiceListSerializer,
    BroadcastSerializer,
    BroadcastListSerializer,
    PaymentGatewaySerializer,
    PaymentGatewayListSerializer,
)
from accounts.permissions import IsSuperAdmin


class PlanViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Plan instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Plan.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "price_monthly", "price_yearly"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return PlanListSerializer
        return PlanSerializer


class SubscriptionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Subscription instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage subscriptions
    """

    queryset = Subscription.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["company__name", "plan__name"]
    ordering_fields = ["created_at", "start_date", "end_date"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return SubscriptionListSerializer
        return SubscriptionSerializer


class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Payment instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage payments
    """

    queryset = Payment.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["payment_status", "payment_method", "subscription__company__name"]
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return PaymentListSerializer
        return PaymentSerializer


class InvoiceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Invoice instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage invoices
    """

    queryset = Invoice.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["invoice_number", "subscription__company__name", "status"]
    ordering_fields = ["created_at", "due_date", "amount"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return InvoiceListSerializer
        return InvoiceSerializer

    @action(detail=True, methods=['post'])
    def mark_paid(self, request, pk=None):
        """Mark an invoice as paid"""
        invoice = self.get_object()
        invoice.status = 'paid'
        invoice.save()
        return Response({'status': 'Invoice marked as paid'})


class BroadcastViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Broadcast instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Broadcast.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["subject", "content", "target", "status"]
    ordering_fields = ["created_at", "scheduled_at", "sent_at"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return BroadcastListSerializer
        return BroadcastSerializer

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """Send a broadcast immediately"""
        broadcast = self.get_object()
        if broadcast.status == 'sent':
            return Response({'error': 'Broadcast already sent'}, status=status.HTTP_400_BAD_REQUEST)
        
        broadcast.status = 'sent'
        broadcast.sent_at = timezone.now()
        broadcast.save()
        # TODO: Implement actual sending logic (email, notifications, etc.)
        return Response({'status': 'Broadcast sent successfully'})

    @action(detail=True, methods=['post'])
    def schedule(self, request, pk=None):
        """Schedule a broadcast for later"""
        broadcast = self.get_object()
        scheduled_at = request.data.get('scheduled_at')
        if not scheduled_at:
            return Response({'error': 'scheduled_at is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        broadcast.status = 'scheduled'
        broadcast.scheduled_at = scheduled_at
        broadcast.save()
        return Response({'status': 'Broadcast scheduled successfully'})


class PaymentGatewayViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing PaymentGateway instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = PaymentGateway.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description", "status"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_serializer_class(self):
        if self.action == "list":
            return PaymentGatewayListSerializer
        return PaymentGatewaySerializer

    @action(detail=True, methods=['post'])
    def toggle_enabled(self, request, pk=None):
        """Toggle gateway enabled status"""
        gateway = self.get_object()
        if gateway.status == 'setup_required':
            return Response({'error': 'Gateway setup required before enabling'}, status=status.HTTP_400_BAD_REQUEST)
        
        gateway.enabled = not gateway.enabled
        gateway.status = 'active' if gateway.enabled else 'disabled'
        gateway.save()
        return Response({'status': f'Gateway {"enabled" if gateway.enabled else "disabled"}', 'enabled': gateway.enabled})
