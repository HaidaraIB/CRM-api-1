from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from .models import Product, ProductCategory, Supplier


class ProductCategorySerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    parent_category_name = serializers.CharField(source="parent_category.name", read_only=True)

    class Meta:
        model = ProductCategory
        fields = [
            "id",
            "name",
            "code",
            "description",
            "parent_category",
            "parent_category_name",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class ProductCategoryListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)
    parent_category_name = serializers.CharField(source="parent_category.name", read_only=True)

    class Meta:
        model = ProductCategory
        fields = [
            "id",
            "name",
            "code",
            "description",
            "parent_category",
            "parent_category_name",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="Product")
class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "code",
            "description",
            "category",
            "category_name",
            "price",
            "cost",
            "stock",
            "supplier",
            "supplier_name",
            "sku",
            "is_active",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class ProductListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    category_name = serializers.CharField(source="category.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, allow_null=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "code",
            "description",
            "category",
            "category_name",
            "price",
            "cost",
            "stock",
            "supplier",
            "supplier_name",
            "sku",
            "is_active",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="Supplier")
class SupplierSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "code",
            "phone",
            "email",
            "address",
            "contact_person",
            "specialization",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class SupplierListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "code",
            "phone",
            "email",
            "address",
            "contact_person",
            "specialization",
            "company",
            "company_name",
            "created_at",
        ]

