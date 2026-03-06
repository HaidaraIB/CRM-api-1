from rest_framework import serializers
from .models import SupportTicket


class SupportTicketSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "title",
            "description",
            "status",
            "company",
            "company_name",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "company",
            "created_by",
            "created_at",
            "updated_at",
        ]

class SupportTicketListSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    company_name = serializers.CharField(source="company.name", read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "title",
            "description",
            "status",
            "company_name",
            "created_by_username",
            "created_at",
            "updated_at",
        ]


class SupportTicketStatusSerializer(serializers.ModelSerializer):
    """For PATCH: only status is writable."""

    class Meta:
        model = SupportTicket
        fields = ["status"]
