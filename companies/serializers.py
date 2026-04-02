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
            "specialization",
            "owner",
            "owner_username",
            "owner_email",
            "auto_assign_enabled",
            "re_assign_enabled",
            "re_assign_hours",
            "free_trial_consumed",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class CompanyListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    owner_email = serializers.CharField(source="owner.email", read_only=True)
    owner_phone = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "domain",
            "specialization",
            "owner",
            "owner_username",
            "owner_email",
            "owner_phone",
            "free_trial_consumed",
            "created_at",
        ]

    def get_owner_phone(self, obj):
        return getattr(obj.owner, "phone", None) or ""

