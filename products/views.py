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
        company = serializer.validated_data['company']
        # البحث عن آخر category لهذه الشركة مع code يبدأ بـ CAT
        last_category = ProductCategory.objects.filter(
            company=company,
            code__startswith='CAT'
        ).order_by('-id').first()
        
        new_num = 1
        if last_category and last_category.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_category.code.replace('CAT', '').strip()
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
        company = serializer.validated_data['company']
        # البحث عن آخر product لهذه الشركة مع code يبدأ بـ PRD
        last_product = Product.objects.filter(
            company=company,
            code__startswith='PRD'
        ).order_by('-id').first()
        
        new_num = 1
        if last_product and last_product.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_product.code.replace('PRD', '').strip()
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
        company = serializer.validated_data['company']
        # البحث عن آخر supplier لهذه الشركة مع code يبدأ بـ SUP
        last_supplier = Supplier.objects.filter(
            company=company,
            code__startswith='SUP'
        ).order_by('-id').first()
        
        new_num = 1
        if last_supplier and last_supplier.code:
            try:
                # استخراج الرقم من آخر code
                code_suffix = last_supplier.code.replace('SUP', '').strip()
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

