from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from .models import Company
from .serializers import CompanySerializer, CompanyListSerializer


class CompanyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Company instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """
    queryset = Company.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "domain", "owner__username"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return CompanyListSerializer
        return CompanySerializer
