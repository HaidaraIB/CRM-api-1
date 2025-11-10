from rest_framework import serializers
from .models import Company


class CompanySerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    owner_email = serializers.CharField(source="owner.email", read_only=True)

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "domain",
            "owner",
            "owner_username",
            "owner_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class CompanyListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    owner_username = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "domain",
            "owner",
            "owner_username",
            "created_at",
        ]

