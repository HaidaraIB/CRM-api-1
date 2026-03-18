from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from django.urls import reverse
from .models import (
    Channel,
    LeadStage,
    LeadStatus,
    CallMethod,
    SMTPSettings,
    SystemBackup,
    SystemAuditLog,
    SystemSettings,
    PlatformTwilioSettings,
)


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
            "is_default",
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
            "is_default",
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
            "is_default",
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
            "is_default",
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


@extend_schema_serializer(component_name="CallMethod")
class CallMethodSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = CallMethod
        fields = [
            "id",
            "name",
            "description",
            "color",
            "company",
            "company_name",
            "is_active",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class CallMethodListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = CallMethod
        fields = [
            "id",
            "name",
            "description",
            "color",
            "company",
            "company_name",
            "is_active",
            "is_default",
            "created_at",
        ]


@extend_schema_serializer(component_name="SMTPSettings")
class SMTPSettingsSerializer(serializers.ModelSerializer):
    """Serializer for SMTP Settings"""
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = SMTPSettings
        fields = [
            "id",
            "host",
            "port",
            "use_tls",
            "use_ssl",
            "username",
            "password",
            "from_email",
            "from_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, data):
        """Validate SMTP settings"""
        if data.get('use_tls') and data.get('use_ssl'):
            raise serializers.ValidationError("Cannot use both TLS and SSL. Choose one.")
        return data


class PlatformTwilioSettingsSerializer(serializers.ModelSerializer):
    """Platform Twilio for admin SMS broadcast. Auth token is write-only and stored encrypted."""
    auth_token_masked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PlatformTwilioSettings
        fields = [
            "id",
            "account_sid",
            "twilio_number",
            "auth_token",
            "auth_token_masked",
            "sender_id",
            "is_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"auth_token": {"write_only": True, "required": False}}

    def get_auth_token_masked(self, obj):
        if obj.auth_token:
            return "********"
        return None

    def update(self, instance, validated_data):
        auth = validated_data.pop("auth_token", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if auth is not None:
            instance.set_auth_token(auth)
        instance.save()
        return instance


class SystemBackupSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    created_by_email = serializers.CharField(source="created_by.email", read_only=True)

    class Meta:
        model = SystemBackup
        fields = [
            "id",
            "status",
            "initiator",
            "file",
            "file_size",
            "created_by",
            "created_by_email",
            "notes",
            "error_message",
            "metadata",
            "created_at",
            "completed_at",
            "download_url",
        ]
        read_only_fields = [
            "id",
            "file",
            "file_size",
            "status",
            "created_by",
            "created_by_email",
            "error_message",
            "metadata",
            "created_at",
            "completed_at",
            "download_url",
        ]

    def get_download_url(self, obj):
        request = self.context.get("request")
        if not request or not obj.file:
            return None
        return request.build_absolute_uri(
            reverse("systembackup-download", args=[obj.pk])
        )


class SystemAuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source="actor.email", read_only=True)

    class Meta:
        model = SystemAuditLog
        fields = [
            "id",
            "action",
            "message",
            "metadata",
            "actor",
            "actor_email",
            "ip_address",
            "created_at",
        ]
        read_only_fields = fields


@extend_schema_serializer(component_name="SystemSettings")
class SystemSettingsSerializer(serializers.ModelSerializer):
    """Serializer for System Settings"""

    class Meta:
        model = SystemSettings
        fields = [
            "id",
            "usd_to_iqd_rate",
            "backup_schedule",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


