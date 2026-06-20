"""Serializers for PBX / ZYCOO integration."""

from __future__ import annotations

from django.conf import settings as django_settings
from rest_framework import serializers

from accounts.models import User
from crm.models import Client
from integrations.encryption import decrypt_token, encrypt_token
from integrations.models import (
    PbxDialCommand,
    PbxSettings,
    SoftphonePlatform,
    UserPbxExtension,
    UserSoftphoneDevice,
)
from integrations.pbx_connector_meta import get_pbx_connector_version


class PbxSettingsSerializer(serializers.ModelSerializer):
    ami_password_masked = serializers.SerializerMethodField(read_only=True)
    webhook_url = serializers.SerializerMethodField(read_only=True)
    connector_online = serializers.SerializerMethodField(read_only=True)
    connector_package_version = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PbxSettings
        fields = [
            "id",
            "provider",
            "pbx_host",
            "ami_port",
            "ami_username",
            "ami_password",
            "ami_password_masked",
            "webhook_token",
            "webhook_url",
            "connector_api_key",
            "connector_install_key",
            "connector_package_version",
            "is_enabled",
            "auto_log_calls",
            "screen_pop_enabled",
            "softphone_enabled",
            "sip_domain",
            "sip_port",
            "sip_transport",
            "wss_uri",
            "stun_server",
            "turn_server",
            "connector_last_seen_at",
            "connector_online",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "webhook_token",
            "connector_api_key",
            "connector_install_key",
            "connector_last_seen_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "ami_password": {"write_only": True, "required": False},
        }

    def get_ami_password_masked(self, obj):
        if obj.ami_password:
            return "••••••••••••••••"
        return None

    def get_webhook_url(self, obj):
        request = self.context.get("request")
        base = ""
        if request:
            base = request.build_absolute_uri("/").rstrip("/")
        elif getattr(django_settings, "PUBLIC_API_BASE_URL", ""):
            base = django_settings.PUBLIC_API_BASE_URL.rstrip("/")
        if not base:
            return f"/api/integrations/webhooks/pbx/{obj.webhook_token}/"
        return f"{base}/api/integrations/webhooks/pbx/{obj.webhook_token}/"

    def get_connector_online(self, obj):
        if not obj.connector_last_seen_at:
            return False
        from django.utils import timezone
        from datetime import timedelta

        return obj.connector_last_seen_at >= timezone.now() - timedelta(minutes=3)

    def get_connector_package_version(self, obj):
        return get_pbx_connector_version()

    def update(self, instance, validated_data):
        pwd = validated_data.pop("ami_password", None)
        if pwd:
            instance.ami_password = encrypt_token(pwd)
        return super().update(instance, validated_data)


class UserPbxExtensionSerializer(serializers.ModelSerializer):
    user_id = serializers.PrimaryKeyRelatedField(
        source="user", queryset=User.objects.all()
    )
    username = serializers.CharField(source="user.username", read_only=True)
    sip_password_masked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = UserPbxExtension
        fields = [
            "id",
            "user_id",
            "username",
            "extension",
            "sip_password",
            "sip_password_masked",
            "softphone_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "sip_password": {"write_only": True, "required": False},
        }

    def get_sip_password_masked(self, obj):
        if obj.sip_password:
            return "••••••••••••••••"
        return None

    def validate_user(self, user):
        request = self.context.get("request")
        if request and user.company_id != request.user.company_id:
            raise serializers.ValidationError("User must belong to your company.")
        return user

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        if not request:
            return attrs

        company = request.user.company
        instance = self.instance
        user = attrs.get("user") or (instance.user if instance else None)
        extension = attrs.get("extension") or (instance.extension if instance else None)

        if extension:
            qs = UserPbxExtension.objects.filter(company=company, extension=extension)
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"extension": "This extension is already mapped to another user."}
                )

        if user:
            qs = UserPbxExtension.objects.filter(user=user)
            if instance:
                qs = qs.exclude(pk=instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"user_id": "This user already has an extension mapping."}
                )

        return attrs

    def create(self, validated_data):
        pwd = validated_data.pop("sip_password", None)
        obj = super().create(validated_data)
        if pwd:
            obj.sip_password = encrypt_token(pwd)
            obj.save(update_fields=["sip_password", "updated_at"])
        return obj

    def update(self, instance, validated_data):
        pwd = validated_data.pop("sip_password", None)
        obj = super().update(instance, validated_data)
        if pwd:
            obj.sip_password = encrypt_token(pwd)
            obj.save(update_fields=["sip_password", "updated_at"])
        return obj


class SoftphoneDeviceSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=SoftphonePlatform.choices)
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    fcm_token = serializers.CharField(max_length=512, required=False, allow_blank=True, default="")
    voip_token = serializers.CharField(max_length=512, required=False, allow_blank=True, default="")


class PbxDialRequestSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    phone_number = serializers.CharField(max_length=32, required=False, allow_blank=True)
    extension = serializers.CharField(max_length=32, required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context.get("request")
        client = attrs["client"]
        if request and client.company_id != request.user.company_id:
            raise serializers.ValidationError({"client": "Lead not in your company."})
        phone = (attrs.get("phone_number") or "").strip()
        if not phone:
            phone = (client.phone or "").strip()
        if not phone:
            raise serializers.ValidationError({"phone_number": "No phone number available."})
        attrs["phone_number"] = phone
        return attrs


class PbxDialCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = PbxDialCommand
        fields = [
            "id",
            "phone_number",
            "extension",
            "status",
            "result_message",
            "created_at",
            "processed_at",
        ]
