from rest_framework import serializers

from .models import GuideArticle, NewsPost


def cover_image_absolute_url(obj, request):
    if not obj.cover_image:
        return None
    url = obj.cover_image.url
    if request:
        return request.build_absolute_uri(url)
    return url


class GuideArticleSerializer(serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()

    class Meta:
        model = GuideArticle
        fields = [
            "id",
            "title_en",
            "title_ar",
            "body_en",
            "body_ar",
            "slug",
            "sort_order",
            "is_published",
            "cover_image",
            "cover_image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "cover_image_url"]
        extra_kwargs = {
            "cover_image": {"write_only": True, "required": False, "allow_null": True},
            "slug": {"required": False, "allow_blank": True},
        }

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))


class GuideArticleListSerializer(serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()

    class Meta:
        model = GuideArticle
        fields = [
            "id",
            "title_en",
            "title_ar",
            "slug",
            "sort_order",
            "is_published",
            "cover_image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))


class NewsPostSerializer(serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()
    is_notified = serializers.SerializerMethodField()

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
        ]
        extra_kwargs = {
            "cover_image": {"write_only": True, "required": False, "allow_null": True},
        }

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))

    def get_is_notified(self, obj):
        return bool(obj.notified_at)


class NewsPostListSerializer(serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()
    is_notified = serializers.SerializerMethodField()

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
            "cover_image_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_cover_image_url(self, obj):
        return cover_image_absolute_url(obj, self.context.get("request"))

    def get_is_notified(self, obj):
        return bool(obj.notified_at)
