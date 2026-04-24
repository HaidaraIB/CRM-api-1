from django.contrib import admin
from .models import (
    Channel,
    LeadStage,
    LeadStatus,
    SMTPSettings,
    SystemBackup,
    SystemAuditLog,
    CallMethod,
    VisitType,
    SystemSettings,
    BillingSettings,
)


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ['name', 'type', 'priority', 'company', 'is_active', 'created_at']
    list_filter = ['priority', 'is_active', 'created_at']
    search_fields = ['name', 'type']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(LeadStage)
class LeadStageAdmin(admin.ModelAdmin):
    list_display = ['name', 'order', 'required', 'auto_advance', 'company', 'is_active', 'created_at']
    list_filter = ['required', 'auto_advance', 'is_active', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(LeadStatus)
class LeadStatusAdmin(admin.ModelAdmin):
    list_display = ['name', 'automation_key', 'category', 'is_default', 'is_hidden', 'company', 'is_active', 'created_at']
    list_filter = ['category', 'is_default', 'is_hidden', 'is_active', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SMTPSettings)
class SMTPSettingsAdmin(admin.ModelAdmin):
    list_display = ["from_email", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["from_email", "host"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Resend outbound email",
            {
                "description": "Set RESEND_API_KEY on the server. Verify your sending domain in the Resend dashboard.",
                "fields": ("from_email", "from_name", "is_active"),
            },
        ),
        (
            "Legacy (unused for sending)",
            {
                "classes": ("collapse",),
                "fields": ("host", "port", "use_tls", "use_ssl", "username", "password"),
            },
        ),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(SystemBackup)
class SystemBackupAdmin(admin.ModelAdmin):
    list_display = ['id', 'status', 'initiator', 'created_by', 'file_size', 'created_at']
    list_filter = ['status', 'initiator', 'created_at']
    search_fields = ['id', 'notes', 'created_by__email']
    readonly_fields = ['created_at', 'completed_at', 'file', 'file_size', 'metadata']


@admin.register(SystemAuditLog)
class SystemAuditLogAdmin(admin.ModelAdmin):
    list_display = ['action', 'actor', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['action', 'message', 'metadata']
    readonly_fields = ['action', 'message', 'metadata', 'actor', 'ip_address', 'created_at']


@admin.register(CallMethod)
class CallMethodAdmin(admin.ModelAdmin):
    list_display = ['name', 'color', 'company', 'is_active', 'created_at', 'updated_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(VisitType)
class VisitTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'color', 'company', 'is_active', 'is_default', 'created_at', 'updated_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "usd_to_iqd_rate",
        "backup_schedule",
        "mobile_minimum_version_android",
        "mobile_minimum_version_ios",
        "updated_at",
    ]
    list_filter = ["backup_schedule"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "General",
            {
                "fields": ("usd_to_iqd_rate", "backup_schedule"),
            },
        ),
        (
            "Mobile app — forced update",
            {
                "description": "Leave minimum version empty for an OS to allow any version. "
                "Set minimum semver and store URLs when you want old builds to block until update.",
                "fields": (
                    "mobile_minimum_version_android",
                    "mobile_minimum_build_android",
                    "mobile_store_url_android",
                    "mobile_minimum_version_ios",
                    "mobile_minimum_build_ios",
                    "mobile_store_url_ios",
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(BillingSettings)
class BillingSettingsAdmin(admin.ModelAdmin):
    list_display = ["id", "issuer_name", "issuer_email", "updated_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Issuer", {"fields": ("issuer_name", "issuer_address", "issuer_email", "issuer_phone", "issuer_tax_id", "logo")}),
        ("Invoice copy", {"fields": ("footer_text", "payment_instructions")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


