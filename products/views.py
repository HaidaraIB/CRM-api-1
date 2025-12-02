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
    """
    ViewSet for managing ProductCategory instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = ProductCategory.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessProductCategory]
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
        last_category = ProductCategory.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_category and last_category.code:
            try:
                last_num = int(last_category.code.replace('CAT', ''))
                new_code = f"CAT{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "CAT001"
        else:
            new_code = "CAT001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ProductCategoryListSerializer
        return ProductCategorySerializer


class ProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Product instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Product.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessProduct]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code", "category__name"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        return queryset.filter(company=user.company)

    def perform_create(self, serializer):
        # توليد code تلقائياً
        last_product = Product.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_product and last_product.code:
            try:
                last_num = int(last_product.code.replace('PRD', ''))
                new_code = f"PRD{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "PRD001"
        else:
            new_code = "PRD001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return ProductListSerializer
        return ProductSerializer


class SupplierViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Supplier instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Supplier.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessSupplier]
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
        last_supplier = Supplier.objects.filter(company=serializer.validated_data['company']).order_by('-code').first()
        if last_supplier and last_supplier.code:
            try:
                last_num = int(last_supplier.code.replace('SUP', ''))
                new_code = f"SUP{str(last_num + 1).zfill(3)}"
            except ValueError:
                new_code = "SUP001"
        else:
            new_code = "SUP001"
        serializer.save(code=new_code)

    def get_serializer_class(self):
        if self.action == "list":
            return SupplierListSerializer
        return SupplierSerializer

