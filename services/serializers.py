from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from .models import ServiceProvider, Service, ServicePackage


class ServiceSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    provider_name = serializers.CharField(source="provider.name", read_only=True)

    class Meta:
        model = Service
        fields = [
            "id",
            "name",
            "code",
            "description",
            "category",
            "price",
            "duration",
            "provider",
            "provider_name",
            "is_active",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class ServiceListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)
    provider_name = serializers.CharField(source="provider.name", read_only=True, allow_null=True)

    class Meta:
        model = Service
        fields = [
            "id",
            "name",
            "code",
            "description",
            "category",
            "price",
            "duration",
            "provider",
            "provider_name",
            "is_active",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="ServicePackage")
class ServicePackageSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    services = serializers.PrimaryKeyRelatedField(many=True, read_only=False, queryset=Service.objects.all())

    class Meta:
        model = ServicePackage
        fields = [
            "id",
            "name",
            "code",
            "description",
            "price",
            "duration",
            "services",
            "is_active",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class ServicePackageListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = ServicePackage
        fields = [
            "id",
            "name",
            "code",
            "description",
            "price",
            "duration",
            "is_active",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="ServiceProvider")
class ServiceProviderSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = ServiceProvider
        fields = [
            "id",
            "name",
            "code",
            "phone",
            "email",
            "specialization",
            "rating",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class ServiceProviderListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = ServiceProvider
        fields = [
            "id",
            "name",
            "code",
            "phone",
            "email",
            "specialization",
            "rating",
            "company",
            "company_name",
            "created_at",
        ]

