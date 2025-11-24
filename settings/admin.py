from django.contrib import admin
from .models import Channel, LeadStage, LeadStatus, SMTPSettings


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
    list_display = ['name', 'category', 'is_default', 'is_hidden', 'company', 'is_active', 'created_at']
    list_filter = ['category', 'is_default', 'is_hidden', 'is_active', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SMTPSettings)
class SMTPSettingsAdmin(admin.ModelAdmin):
    list_display = ['host', 'port', 'from_email', 'is_active', 'updated_at']
    list_filter = ['is_active', 'use_tls', 'use_ssl']
    search_fields = ['host', 'from_email', 'username']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('SMTP Server', {
            'fields': ('host', 'port', 'use_tls', 'use_ssl')
        }),
        ('Authentication', {
            'fields': ('username', 'password')
        }),
        ('Email Settings', {
            'fields': ('from_email', 'from_name', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


