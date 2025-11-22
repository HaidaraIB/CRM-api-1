from django.contrib import admin
from .models import Developer, Project, Unit, Owner


@admin.register(Developer)
class DeveloperAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "company", "created_at"]
    list_filter = ["company", "created_at"]
    search_fields = ["name", "code"]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "developer", "company", "created_at"]
    list_filter = ["company", "developer", "created_at"]
    search_fields = ["name", "code"]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "project", "company", "is_sold", "created_at"]
    list_filter = ["company", "project", "is_sold", "created_at"]
    search_fields = ["name", "code"]


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "phone", "company", "created_at"]
    list_filter = ["company", "created_at"]
    search_fields = ["name", "code", "phone"]
