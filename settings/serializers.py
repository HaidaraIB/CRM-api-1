from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from django.urls import reverse
from .models import (
    Channel,
    LeadStage,
    LeadStatus,
    CallMethod,
    VisitType,
    SMTPSettings,
    SystemBackup,
    SystemAuditLog,
    SystemSettings,
    PlatformTwilioSettings,
    BillingSettings,
)
from integrations.policy import (
    INTEGRATION_POLICY_DEFAULTS,
    INTEGRATION_POLICY_PLATFORMS,
    apply_integration_policy_side_effects,
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
            "automation_key",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "automation_key"]


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
            "automation_key",
            "created_at",
        ]
        read_only_fields = ["automation_key"]


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


@extend_schema_serializer(component_name="VisitType")
class VisitTypeSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = VisitType
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


class VisitTypeListSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = VisitType
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
    """Serializer for platform outbound email (Resend). Legacy SMTP fields are kept for API compatibility."""
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
        extra_kwargs = {
            "from_name": {
                "help_text": "Inbox display name. Leave blank to use LOOP CRM (override with PLATFORM_EMAIL_SENDER_DISPLAY_NAME).",
                "required": False,
                "allow_blank": True,
            },
        }

    def validate(self, data):
        """Validate stored settings (legacy TLS/SSL mutual exclusion)."""
        if data.get("use_tls") and data.get("use_ssl"):
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

    integration_policies = serializers.JSONField(required=False)

    class Meta:
        model = SystemSettings
        fields = [
            "id",
            "usd_to_iqd_rate",
            "backup_schedule",
            "mobile_minimum_version_android",
            "mobile_minimum_version_ios",
            "mobile_minimum_build_android",
            "mobile_minimum_build_ios",
            "mobile_store_url_android",
            "mobile_store_url_ios",
            "integration_policies",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_integration_policies(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("integration_policies must be a JSON object.")
        normalized = {}
        for platform in INTEGRATION_POLICY_PLATFORMS:
            raw = value.get(platform) or {}
            if not isinstance(raw, dict):
                raw = {}
            global_enabled = raw.get("global_enabled", INTEGRATION_POLICY_DEFAULTS["global_enabled"])
            global_message = (raw.get("global_message") or "").strip()
            company_overrides_raw = raw.get("company_overrides") or {}
            company_overrides = {}
            if isinstance(company_overrides_raw, dict):
                for company_id, company_policy in company_overrides_raw.items():
                    if not isinstance(company_policy, dict):
                        continue
                    company_overrides[str(company_id)] = {
                        "enabled": bool(company_policy.get("enabled", True)),
                        "message": (company_policy.get("message") or "").strip(),
                    }
            normalized[platform] = {
                "global_enabled": bool(global_enabled),
                "global_message": global_message,
                "company_overrides": company_overrides,
            }
        return normalized

    def update(self, instance, validated_data):
        previous_policies = instance.integration_policies or {}
        instance = super().update(instance, validated_data)
        if "integration_policies" in validated_data:
            apply_integration_policy_side_effects(
                previous_policies=previous_policies,
                new_policies=instance.integration_policies or {},
            )
        return instance


class BillingSettingsSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BillingSettings
        fields = [
            "id",
            "issuer_name",
            "issuer_address",
            "issuer_email",
            "issuer_phone",
            "issuer_tax_id",
            "footer_text",
            "payment_instructions",
            "logo",
            "logo_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "logo_url", "created_at", "updated_at"]

    def get_logo_url(self, obj):
        if not obj.logo:
            return None
        request = self.context.get("request")
        try:
            url = obj.logo.url
        except Exception:
            return None
        if request:
            return request.build_absolute_uri(url)
        return url

