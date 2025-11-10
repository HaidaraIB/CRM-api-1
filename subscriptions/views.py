from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from .models import Plan, Subscription, Payment
from .serializers import (
    PlanSerializer,
    PlanListSerializer,
    SubscriptionSerializer,
    SubscriptionListSerializer,
    PaymentSerializer,
    PaymentListSerializer,
)


class PlanViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Plan instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """
    queryset = Plan.objects.all()
    permission_classes = [IsAuthenticated]
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
    """
    queryset = Subscription.objects.all()
    permission_classes = [IsAuthenticated]
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
    """
    queryset = Payment.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["payment_status", "payment_method", "subscription__company__name"]
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return PaymentListSerializer
        return PaymentSerializer
