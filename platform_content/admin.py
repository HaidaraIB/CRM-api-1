from django.contrib import admin

from .models import GuideArticle, NewsPost


@admin.register(GuideArticle)
class GuideArticleAdmin(admin.ModelAdmin):
    list_display = [
        "title_en",
        "slug",
        "sort_order",
        "is_published",
        "updated_at",
    ]
    list_filter = ["is_published"]
    search_fields = ["title_en", "title_ar", "slug"]
    prepopulated_fields = {"slug": ("title_en",)}
    ordering = ["sort_order", "-updated_at"]


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    list_display = [
        "title_en",
        "is_published",
        "published_at",
        "updated_at",
    ]
    list_filter = ["is_published"]
    search_fields = ["title_en", "title_ar"]
    ordering = ["-published_at", "-created_at"]
