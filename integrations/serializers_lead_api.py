"""
Serializers for Custom Lead API (inbound + key management).
"""
from rest_framework import serializers

from crm.models import Campaign
from settings.models import Channel, LeadStatus


class InboundLeadSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    external_id = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    campaign_id = serializers.IntegerField(required=False, allow_null=True)
    communication_way_id = serializers.IntegerField(required=False, allow_null=True)
    status_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(
        choices=["low", "medium", "high"],
        required=False,
        default="medium",
    )
    type = serializers.ChoiceField(
        choices=["fresh", "hot", "cold"],
        required=False,
        default="fresh",
    )
    custom_fields = serializers.DictField(
        child=serializers.JSONField(),
        required=False,
        allow_null=True,
    )

    def __init__(self, *args, company=None, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)

    def validate_campaign_id(self, value):
        if value is None:
            return None
        if not Campaign.objects.filter(id=value, company=self.company).exists():
            raise serializers.ValidationError("Campaign not found for this company.")
        return value

    def validate_communication_way_id(self, value):
        if value is None:
            return None
        if not Channel.objects.filter(id=value, company=self.company).exists():
            raise serializers.ValidationError("Communication channel not found for this company.")
        return value

    def validate_status_id(self, value):
        if value is None:
            return None
        if not LeadStatus.objects.filter(id=value, company=self.company).exists():
            raise serializers.ValidationError("Lead status not found for this company.")
        return value


class CompanyLeadApiKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)


class CompanyLeadApiKeyListSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    key_prefix = serializers.CharField()
    key_suffix = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    last_used_at = serializers.DateTimeField(allow_null=True)
