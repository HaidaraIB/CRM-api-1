from rest_framework import serializers
from .models import SupportTicket, SupportTicketAttachment


class SupportTicketAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicketAttachment
        fields = ["id", "file", "url", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_url(self, obj):
        request = self.context.get("request")
        if request and obj.file:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url if obj.file else None


class SupportTicketSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True
    )
    company_name = serializers.CharField(source="company.name", read_only=True)
    attachments = SupportTicketAttachmentSerializer(many=True, read_only=True)

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
            "attachments",
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
    attachments = SupportTicketAttachmentSerializer(many=True, read_only=True)

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
            "attachments",
        ]


class SupportTicketStatusSerializer(serializers.ModelSerializer):
    """For PATCH: only status is writable."""

    class Meta:
        model = SupportTicket
        fields = ["status"]
