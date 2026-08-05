from django.contrib import admin

from .models import CompanyLibraryFile


@admin.register(CompanyLibraryFile)
class CompanyLibraryFileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_filename",
        "company",
        "kind",
        "size_bytes",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("kind",)
    search_fields = ("original_filename", "company__name")
    readonly_fields = ("created_at", "updated_at", "size_bytes", "mime_type", "kind")
