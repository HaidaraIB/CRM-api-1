from rest_framework import serializers

from .models import CompanyLibraryFile


class CompanyLibraryFileSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CompanyLibraryFile
        fields = (
            "id",
            "original_filename",
            "mime_type",
            "size_bytes",
            "kind",
            "uploaded_by",
            "uploaded_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        user = obj.uploaded_by
        if not user:
            return None
        full = (user.get_full_name() or "").strip()
        return full or user.username


class CompanyLibraryFileRenameSerializer(serializers.Serializer):
    original_filename = serializers.CharField(max_length=255, trim_whitespace=True)

    def validate_original_filename(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Filename is required.")
        return name
