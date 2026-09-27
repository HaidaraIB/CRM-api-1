from django.contrib import admin

from .models import SupportConversation, SupportMessage


class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0
    readonly_fields = ("created_at", "side", "sender")
    fields = ("side", "sender", "body", "created_at")


@admin.register(SupportConversation)
class SupportConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "status", "last_message_at", "last_message_side")
    list_filter = ("status",)
    search_fields = ("company__name", "company__domain")
    inlines = [SupportMessageInline]
    raw_id_fields = ("company", "resolved_by", "tenant_last_read_message", "support_last_read_message")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "side", "sender", "created_at")
    list_filter = ("side",)
    raw_id_fields = ("conversation", "sender", "reply_to")
