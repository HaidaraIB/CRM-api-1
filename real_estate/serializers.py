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
    code = serializers.CharField(required=False, allow_blank=False)

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
            "lounge",
            "area",
            "currency",
            "is_sold",
            "company",
            "company_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_code(self, value):
        normalized_code = value.strip().upper()
        if not normalized_code:
            raise serializers.ValidationError("Code cannot be empty.")
        return normalized_code

    def validate(self, attrs):
        attrs = super().validate(attrs)

        code = attrs.get("code")
        if code is None:
            return attrs

        company = attrs.get("company")
        if company is None and self.instance is not None:
            company = self.instance.company

        if company is None:
            raise serializers.ValidationError({"company": "Company is required when setting a code."})

        queryset = Unit.objects.filter(company=company, code=code)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError({"code": "A unit with this code already exists in your company."})

        return attrs


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
            "lounge",
            "area",
            "currency",
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

