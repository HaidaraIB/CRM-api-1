from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from .models import Client, Deal, Task
from .serializers import (
    ClientSerializer,
    ClientListSerializer,
    DealSerializer,
    DealListSerializer,
    TaskSerializer,
    TaskListSerializer,
)


class ClientViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Client instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """
    queryset = Client.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "phone_number", "priority", "type", "communication_way"]
    ordering_fields = ["created_at", "name", "priority"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return ClientListSerializer
        return ClientSerializer


class DealViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Deal instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """
    queryset = Deal.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["client__name", "stage", "company__name"]
    ordering_fields = ["created_at", "updated_at", "stage"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return DealListSerializer
        return DealSerializer


class TaskViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Task instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """
    queryset = Task.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["notes", "stage", "deal__client__name"]
    ordering_fields = ["created_at", "reminder_date", "stage"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return TaskListSerializer
        return TaskSerializer
