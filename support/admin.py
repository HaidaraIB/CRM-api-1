from django.contrib import admin
from .models import SupportTicket


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "title",
        "status",
        "company",
        "created_by",
        "created_at",
    ]
    list_filter = ["status", "company", "created_at"]
    search_fields = ["title", "description", "created_by__username", "company__name"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]
