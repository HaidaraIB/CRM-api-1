from rest_framework import serializers
from drf_spectacular.utils import extend_schema_serializer
from django.urls import reverse
from .models import (
    Channel,
    LeadStage,
    LeadStatus,
    CallMethod,
    VisitType,
    Tag,
    SMTPSettings,
    SystemBackup,
    SystemAuditLog,
    SystemSettings,
    PlatformTwilioSettings,
    PlatformOTPIQSettings,
    PlatformWhatsAppSettings,
    BillingSettings,
)
from integrations.policy import (
    INTEGRATION_POLICY_DEFAULTS,
    INTEGRATION_POLICY_PLATFORMS,
    apply_integration_policy_side_effects,
)
from .feature_policy import normalize_feature_policies


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
            "auto_delete_after_hours",
            "requires_change_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "automation_key"]

    def validate_auto_delete_after_hours(self, value):
        if value is not None and value < 1:
            raise serializers.ValidationError("Must be at least 1 or null to disable.")
        return value


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
            "auto_delete_after_hours",
            "requires_change_reason",
            "created_at",
        ]
        read_only_fields = ["automation_key"]

    def validate_auto_delete_after_hours(self, value):
        if value is not None and value < 1:
            raise serializers.ValidationError("Must be at least 1 or null to disable.")
        return value


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


@extend_schema_serializer(component_name="Tag")
class TagSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Tag
        fields = [
            "id",
            "name",
            "description",
            "color",
            "company",
            "company_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TagListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views and nested client output"""
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = Tag
        fields = [
            "id",
            "name",
            "description",
            "color",
            "company",
            "company_name",
            "is_active",
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
        inst = self.instance
        is_active = data.get("is_active", inst.is_active if inst else False)
        from_email = data.get("from_email", inst.from_email if inst else "")
        from settings.credential_validation import validate_resend_outbound_email

        email_errors = validate_resend_outbound_email(
            is_active=bool(is_active),
            from_email=from_email,
        )
        if email_errors:
            raise serializers.ValidationError(email_errors)
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

    def validate(self, attrs):
        from accounts.phone_otp_policy import CHANNEL_TWILIO_SMS, effective_phone_otp_channel
        from settings.credential_validation import validate_twilio_credentials

        inst = self.instance
        account_sid = attrs.get("account_sid", getattr(inst, "account_sid", None) if inst else "")
        twilio_number = attrs.get("twilio_number", getattr(inst, "twilio_number", None) if inst else "")
        sender_id = attrs.get("sender_id", getattr(inst, "sender_id", None) if inst else "")
        auth_token = attrs.get("auth_token")
        if auth_token is None and inst:
            auth_token = inst.get_auth_token()

        errors = validate_twilio_credentials(
            account_sid=account_sid,
            auth_token=auth_token,
            twilio_number=twilio_number,
            sender_id=sender_id,
            require_number_for_registration=effective_phone_otp_channel() == CHANNEL_TWILIO_SMS,
        )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def update(self, instance, validated_data):
        auth = validated_data.pop("auth_token", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if auth is not None:
            instance.set_auth_token(auth)
        instance.save()
        return instance


class PlatformOTPIQSettingsSerializer(serializers.ModelSerializer):
    """Platform OTPIQ for registration OTP. API key is write-only and stored encrypted."""

    api_key_masked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PlatformOTPIQSettings
        fields = [
            "id",
            "api_key",
            "api_key_masked",
            "sender_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"api_key": {"write_only": True, "required": False}}

    def get_api_key_masked(self, obj):
        if obj.api_key:
            return "********"
        return None

    def validate(self, attrs):
        from settings.credential_validation import validate_otpiq_credentials

        inst = self.instance
        sender_id = attrs.get("sender_id", getattr(inst, "sender_id", None) if inst else "")
        api_key = attrs.get("api_key")
        if api_key is None and inst:
            api_key = inst.get_api_key()

        errors = validate_otpiq_credentials(api_key=api_key, sender_id=sender_id)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if api_key is not None:
            instance.set_api_key(api_key)
        instance.save()
        return instance


class PlatformWhatsAppSettingsSerializer(serializers.ModelSerializer):
    """Platform WhatsApp Cloud API. Access token is write-only and stored encrypted."""

    access_token_masked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PlatformWhatsAppSettings
        fields = [
            "id",
            "phone_number_id",
            "access_token",
            "access_token_masked",
            "graph_api_version",
            "otp_template_name",
            "otp_template_lang",
            "admin_template_name",
            "admin_template_lang",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"access_token": {"write_only": True, "required": False}}

    def get_access_token_masked(self, obj):
        if obj.access_token:
            return "********"
        return None

    def validate(self, attrs):
        from settings.credential_validation import validate_whatsapp_platform_credentials

        inst = self.instance
        phone_number_id = attrs.get(
            "phone_number_id", getattr(inst, "phone_number_id", None) if inst else ""
        )
        graph_api_version = attrs.get(
            "graph_api_version", getattr(inst, "graph_api_version", None) if inst else ""
        )
        otp_template_name = attrs.get(
            "otp_template_name", getattr(inst, "otp_template_name", None) if inst else ""
        )
        access_token = attrs.get("access_token")
        if access_token is None and inst:
            access_token = inst.get_access_token()

        errors = validate_whatsapp_platform_credentials(
            phone_number_id=phone_number_id,
            access_token=access_token,
            graph_api_version=graph_api_version,
            otp_template_name=otp_template_name,
        )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def update(self, instance, validated_data):
        token = validated_data.pop("access_token", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if token is not None:
            instance.set_access_token(token)
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
    feature_policies = serializers.JSONField(required=False)

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
            "feature_policies",
            "login_lockout_enabled",
            "login_max_failed_attempts",
            "login_lockout_duration_minutes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_login_max_failed_attempts(self, value):
        if value is not None and value < 1:
            raise serializers.ValidationError("Must be at least 1.")
        return value

    def validate_login_lockout_duration_minutes(self, value):
        if value is not None and value < 1:
            raise serializers.ValidationError("Must be at least 1.")
        return value

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

    def validate_feature_policies(self, value):
        try:
            return normalize_feature_policies(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

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

