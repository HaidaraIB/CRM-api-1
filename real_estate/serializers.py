from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from .models import Developer, Project, Unit, Owner


class DeveloperSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Developer
        fields = [
            "id",
            "name",
            "code",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class DeveloperListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Developer
        fields = [
            "id",
            "name",
            "code",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="Project")
class ProjectSerializer(serializers.ModelSerializer):
    developer_name = serializers.CharField(source="developer.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "code",
            "developer",
            "developer_name",
            "type",
            "city",
            "payment_method",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class ProjectListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    developer_name = serializers.CharField(source="developer.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "code",
            "developer",
            "developer_name",
            "type",
            "city",
            "payment_method",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="Unit")
class UnitSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Unit
        fields = [
            "id",
            "name",
            "code",
            "project",
            "project_name",
            "bedrooms",
            "price",
            "bathrooms",
            "type",
            "finishing",
            "city",
            "district",
            "zone",
            "is_sold",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class UnitListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    project_name = serializers.CharField(source="project.name", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Unit
        fields = [
            "id",
            "name",
            "code",
            "project",
            "project_name",
            "bedrooms",
            "price",
            "bathrooms",
            "type",
            "finishing",
            "city",
            "district",
            "zone",
            "is_sold",
            "company",
            "company_name",
            "created_at",
        ]


@extend_schema_serializer(component_name="Owner")
class OwnerSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Owner
        fields = [
            "id",
            "name",
            "code",
            "phone",
            "city",
            "district",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]


class OwnerListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Owner
        fields = [
            "id",
            "name",
            "code",
            "phone",
            "city",
            "district",
            "company",
            "company_name",
            "created_at",
        ]

