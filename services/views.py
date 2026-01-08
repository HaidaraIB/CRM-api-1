from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin, CanAccessServiceProvider, CanAccessService, CanAccessServicePackage, HasActiveSubscription
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessService]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "category"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        # البحث عن آخر service لهذه الشركة مع code يبدأ بـ SVC
        last_service = Service.objects.filter(
            company=company,
            code__startswith='SVC'
        ).order_by('-id').first()
        
        new_num = 1
        if last_service and last_service.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_service.code.replace('SVC', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"SVC{str(new_num).zfill(3)}"
            if not Service.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique service code")
        
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessServicePackage]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        # البحث عن آخر package لهذه الشركة مع code يبدأ بـ PKG
        last_package = ServicePackage.objects.filter(
            company=company,
            code__startswith='PKG'
        ).order_by('-id').first()
        
        new_num = 1
        if last_package and last_package.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_package.code.replace('PKG', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"PKG{str(new_num).zfill(3)}"
            if not ServicePackage.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique service package code")
        
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
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessServiceProvider]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "phone"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        company = serializer.validated_data['company']
        # البحث عن آخر provider لهذه الشركة مع code يبدأ بـ PRV
        last_provider = ServiceProvider.objects.filter(
            company=company,
            code__startswith='PRV'
        ).order_by('-id').first()
        
        new_num = 1
        if last_provider and last_provider.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_provider.code.replace('PRV', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # التأكد من أن الـ code فريد (في حالة race condition)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"PRV{str(new_num).zfill(3)}"
            if not ServiceProvider.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique service provider code")
        
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ServiceProviderListSerializer
        return ServiceProviderSerializer

