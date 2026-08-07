from rest_framework import serializers

from .models import GuideArticle, GuideCategory, NewsPost, PageHelpVideo
from .youtube import extract_youtube_video_id, youtube_embed_url


def cover_image_absolute_url(obj, request):
    if not obj.cover_image:
        return None
    url = obj.cover_image.url
    if request:
        return request.build_absolute_uri(url)
    return url


class YouTubeUrlMixin:
    """Validate optional youtube_url and expose youtube_embed_url for iframes."""

    youtube_embed_url = serializers.SerializerMethodField()

    def validate_youtube_url(self, value):
        if value in (None, ""):
            return ""
        value = str(value).strip()
        if not value:
            return ""
        if not extract_youtube_video_id(value):
            raise serializers.ValidationError(
                "Enter a valid YouTube URL (watch, youtu.be, shorts, or embed)."
            )
        return value

    def get_youtube_embed_url(self, obj):
        return youtube_embed_url(getattr(obj, "youtube_url", None) or "")


class GuideCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = GuideCategory
        fields = [
            "id",
            "name_en",
            "name_ar",
            "slug",
            "sort_order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "slug": {"required": False, "allow_blank": True},
        }


class GuideCategoryBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = GuideCategory
        fields = ["id", "name_en", "name_ar", "slug", "sort_order"]
        read_only_fields = fields


class GuideArticleSerializer(YouTubeUrlMixin, serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()
    youtube_embed_url = serializers.SerializerMethodField()
    category = GuideCategoryBriefSerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        source="category",
        queryset=GuideCategory.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = GuideArticle
        fields = [
            "id",
            "title_en",
            "title_ar",
            "body_en",
            "body_ar",
            "slug",
            "category",
            "category_id",
            "sort_order",
            "is_published",
            "youtube_url",
            "youtube_embed_url",
            "cover_image",
            "cover_image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "category",
            "created_at",
            "updated_at",
            "cover_image_url",
            "youtube_embed_url",
        ]
        extra_kwargs = {
            "cover_image": {"write_only": True, "required": False, "allow_null": True},
            "slug": {"required": False, "allow_blank": True},
            "youtube_url": {"required": False, "allow_blank": True},
        }

    def to_internal_value(self, data):
        # Multipart forms send "" for cleared nullable FKs.
        if hasattr(data, "copy"):
            data = data.copy()
            if data.get("category_id") == "":
                data["category_id"] = None
        return super().to_internal_value(data)

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))


class GuideArticleListSerializer(serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()
    youtube_embed_url = serializers.SerializerMethodField()
    category = GuideCategoryBriefSerializer(read_only=True)

    class Meta:
        model = GuideArticle
        fields = [
            "id",
            "title_en",
            "title_ar",
            "slug",
            "category",
            "sort_order",
            "is_published",
            "youtube_url",
            "youtube_embed_url",
            "cover_image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))

    def get_youtube_embed_url(self, obj):
        return youtube_embed_url(getattr(obj, "youtube_url", None) or "")


class NewsPostSerializer(YouTubeUrlMixin, serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()
    is_notified = serializers.SerializerMethodField()
    youtube_embed_url = serializers.SerializerMethodField()

    class Meta:
        model = NewsPost
        fields = [
            "id",
            "title_en",
            "title_ar",
            "summary_en",
            "summary_ar",
            "body_en",
            "body_ar",
            "is_published",
            "published_at",
            "notified_at",
            "last_notify_channels",
            "is_notified",
            "youtube_url",
            "youtube_embed_url",
            "cover_image",
            "cover_image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "published_at",
            "notified_at",
            "last_notify_channels",
            "is_notified",
            "created_at",
            "updated_at",
            "cover_image_url",
            "youtube_embed_url",
        ]
        extra_kwargs = {
            "cover_image": {"write_only": True, "required": False, "allow_null": True},
            "youtube_url": {"required": False, "allow_blank": True},
        }

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))

    def get_is_notified(self, obj):
        return bool(obj.notified_at)


class NewsPostListSerializer(serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()
    is_notified = serializers.SerializerMethodField()
    youtube_embed_url = serializers.SerializerMethodField()

    class Meta:
        model = NewsPost
        fields = [
            "id",
            "title_en",
            "title_ar",
            "summary_en",
            "summary_ar",
            "is_published",
            "published_at",
            "notified_at",
            "last_notify_channels",
            "is_notified",
            "youtube_url",
            "youtube_embed_url",
            "cover_image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))

    def get_is_notified(self, obj):
        return bool(obj.notified_at)

    def get_youtube_embed_url(self, obj):
        return youtube_embed_url(getattr(obj, "youtube_url", None) or "")


class PageHelpVideoSerializer(YouTubeUrlMixin, serializers.ModelSerializer):
    youtube_embed_url = serializers.SerializerMethodField()
    page_key_display = serializers.CharField(
        source="get_page_key_display", read_only=True
    )

    class Meta:
        model = PageHelpVideo
        fields = [
            "id",
            "page_key",
            "page_key_display",
            "youtube_url",
            "youtube_embed_url",
            "title_en",
            "title_ar",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "page_key_display",
            "youtube_embed_url",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "youtube_url": {"required": False, "allow_blank": True},
            "title_en": {"required": False, "allow_blank": True},
            "title_ar": {"required": False, "allow_blank": True},
        }


class PageHelpVideoPublicSerializer(serializers.ModelSerializer):
    youtube_embed_url = serializers.SerializerMethodField()

    class Meta:
        model = PageHelpVideo
        fields = [
            "page_key",
            "youtube_url",
            "youtube_embed_url",
            "title_en",
            "title_ar",
        ]
        read_only_fields = fields

    def get_youtube_embed_url(self, obj):
        return youtube_embed_url(getattr(obj, "youtube_url", None) or "")
