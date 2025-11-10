from django.contrib import admin
from .models import Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """Admin configuration for Company model"""

    list_display = [
        "name",
        "domain",
        "owner",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "created_at",
        "updated_at",
    ]
    search_fields = [
        "name",
        "domain",
        "owner__username",
        "owner__email",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Company Information", {"fields": ("name", "domain", "owner")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
