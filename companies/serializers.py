from zoneinfo import ZoneInfo

from rest_framework import serializers
from .models import Company, AdminTenantWhatsAppMessage


class CompanySerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    owner_email = serializers.CharField(source="owner.email", read_only=True)
    owner_phone = serializers.SerializerMethodField()
    owner_phone_verified = serializers.SerializerMethodField()

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
            "owner_phone_verified",
            "auto_assign_enabled",
            "re_assign_enabled",
            "re_assign_hours",
            "free_trial_consumed",
            "timezone",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_timezone(self, value):
        if not value or not str(value).strip():
            return "UTC"
        name = str(value).strip()
        try:
            ZoneInfo(name)
        except Exception:
            raise serializers.ValidationError("timezone must be a valid IANA name (e.g. Asia/Baghdad).")
        return name

    def get_owner_phone(self, obj):
        return getattr(obj.owner, "phone", None) or ""

    def get_owner_phone_verified(self, obj):
        return bool(getattr(obj.owner, "phone_verified", False))


class AdminTenantWhatsAppMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminTenantWhatsAppMessage
        fields = [
            "id",
            "direction",
            "body",
            "whatsapp_message_id",
            "graph_http_status",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "direction",
            "body",
            "whatsapp_message_id",
            "graph_http_status",
            "created_at",
        ]


class CompanyListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    owner_email = serializers.CharField(source="owner.email", read_only=True)
    owner_phone = serializers.SerializerMethodField()
    owner_phone_verified = serializers.SerializerMethodField()

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
            "owner_phone_verified",
            "free_trial_consumed",
            "created_at",
        ]

    def get_owner_phone(self, obj):
        return getattr(obj.owner, "phone", None) or ""

    def get_owner_phone_verified(self, obj):
        return bool(getattr(obj.owner, "phone_verified", False))

