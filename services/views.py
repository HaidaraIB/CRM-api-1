from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin
from .models import Service, ServicePackage, ServiceProvider
from .serializers import (
    ServiceSerializer,
    ServiceListSerializer,
    ServicePackageSerializer,
    ServicePackageListSerializer,
    ServiceProviderSerializer,
    ServiceProviderListSerializer,
)


class ServiceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Service instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Service.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "category"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        last_service = Service.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_service and last_service.code:
            try:
                last_num = int(last_service.code.replace('SVC', ''))
                new_code = f"SVC{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "SVC001"
        else:
            new_code = "SVC001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ServiceListSerializer
        return ServiceSerializer


class ServicePackageViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing ServicePackage instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = ServicePackage.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        last_package = ServicePackage.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_package and last_package.code:
            try:
                last_num = int(last_package.code.replace('PKG', ''))
                new_code = f"PKG{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "PKG001"
        else:
            new_code = "PKG001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ServicePackageListSerializer
        return ServicePackageSerializer


class ServiceProviderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing ServiceProvider instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = ServiceProvider.objects.all()
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "phone"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin():
            return queryset.filter(company=user.company)

        return queryset.none()

    def perform_create(self, serializer):
        # توليد code تلقائياً
        last_provider = ServiceProvider.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_provider and last_provider.code:
            try:
                last_num = int(last_provider.code.replace('PRV', ''))
                new_code = f"PRV{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "PRV001"
        else:
            new_code = "PRV001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ServiceProviderListSerializer
        return ServiceProviderSerializer

