from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from .models import Channel, LeadStage, LeadStatus


@extend_schema_serializer(component_name="Channel")
class ChannelSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Channel
        fields = [
            "id",
            "name",
            "type",
            "priority",
            "company",
            "company_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ChannelListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Channel
        fields = [
            "id",
            "name",
            "type",
            "priority",
            "company",
            "company_name",
            "is_active",
            "created_at",
        ]


@extend_schema_serializer(component_name="LeadStage")
class LeadStageSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = LeadStage
        fields = [
            "id",
            "name",
            "description",
            "color",
            "required",
            "auto_advance",
            "order",
            "company",
            "company_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class LeadStageListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = LeadStage
        fields = [
            "id",
            "name",
            "description",
            "color",
            "required",
            "auto_advance",
            "order",
            "company",
            "company_name",
            "is_active",
            "created_at",
        ]


@extend_schema_serializer(component_name="LeadStatus")
class LeadStatusSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = LeadStatus
        fields = [
            "id",
            "name",
            "description",
            "category",
            "color",
            "is_default",
            "is_hidden",
            "company",
            "company_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class LeadStatusListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = LeadStatus
        fields = [
            "id",
            "name",
            "description",
            "category",
            "color",
            "is_default",
            "is_hidden",
            "company",
            "company_name",
            "is_active",
            "created_at",
        ]


