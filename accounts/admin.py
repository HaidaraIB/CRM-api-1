from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for User model"""

    list_display = [
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "company",
        "is_active",
        "is_staff",
        "date_joined",
    ]
    list_filter = [
        "role",
        "company",
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined",
    ]
    search_fields = [
        "username",
        "email",
        "first_name",
        "last_name",
    ]
    ordering = ["-date_joined"]

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Additional Information", {"fields": ("role", "company")}),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Additional Information", {"fields": ("role", "company", "email")}),
    )
