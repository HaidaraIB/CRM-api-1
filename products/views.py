from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdmin, CanAccessProductCategory, CanAccessProduct, CanAccessSupplier, HasActiveSubscription
from .models import Product, ProductCategory, Supplier
from .serializers import (
    ProductSerializer,
    ProductListSerializer,
    ProductCategorySerializer,
    ProductCategoryListSerializer,
    SupplierSerializer,
    SupplierListSerializer,
)


class ProductCategoryViewSet(viewsets.ModelViewSet):
    """ViewSet for managing ProductCategory instances (CRUD)."""

    queryset = ProductCategory.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessProductCategory]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related("company", "parent_category")
        return queryset.filter(company=user.company)


    def perform_create(self, serializer):
        # Auto-generate sequential code
        company = serializer.validated_data['company']
        # Find the last category code for this company
        last_category = ProductCategory.objects.filter(
            company=company,
            code__startswith='CAT'
        ).order_by('-id').first()
        
        new_num = 1
        if last_category and last_category.code:
            try:
                # Extract the number from the last code
                code_suffix = last_category.code.replace('CAT', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # Ensure uniqueness (handle race conditions)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"CAT{str(new_num).zfill(3)}"
            if not ProductCategory.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique product category code")
        
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ProductCategoryListSerializer
        return ProductCategorySerializer


class ProductViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Product instances (CRUD)."""

    queryset = Product.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessProduct]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "category__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related("company", "category", "supplier")
        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # Auto-generate sequential code
        company = serializer.validated_data['company']
        # Find the last product code for this company
        last_product = Product.objects.filter(
            company=company,
            code__startswith='PRD'
        ).order_by('-id').first()
        
        new_num = 1
        if last_product and last_product.code:
            try:
                # Extract the number from the last code
                code_suffix = last_product.code.replace('PRD', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # Ensure uniqueness (handle race conditions)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"PRD{str(new_num).zfill(3)}"
            if not Product.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique product code")
        
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ProductListSerializer
        return ProductSerializer


class SupplierViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Supplier instances (CRUD)."""

    queryset = Supplier.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessSupplier]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "phone"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset().select_related("company")
        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # Auto-generate sequential code
        company = serializer.validated_data['company']
        # Find the last supplier code for this company
        last_supplier = Supplier.objects.filter(
            company=company,
            code__startswith='SUP'
        ).order_by('-id').first()
        
        new_num = 1
        if last_supplier and last_supplier.code:
            try:
                # Extract the number from the last code
                code_suffix = last_supplier.code.replace('SUP', '').strip()
                if code_suffix:
                    last_num = int(code_suffix)
                    new_num = last_num + 1
            except (ValueError, AttributeError):
                new_num = 1
        
        # Ensure uniqueness (handle race conditions)
        max_attempts = 1000
        attempt = 0
        new_code = None
        
        while attempt < max_attempts:
            candidate_code = f"SUP{str(new_num).zfill(3)}"
            if not Supplier.objects.filter(company=company, code=candidate_code).exists():
                new_code = candidate_code
                break
            new_num += 1
            attempt += 1
        
        if not new_code:
            raise ValueError("Unable to generate unique supplier code")
        
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return SupplierListSerializer
        return SupplierSerializer

