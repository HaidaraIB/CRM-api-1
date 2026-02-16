from django.contrib import admin
from .models import IntegrationAccount, IntegrationLog, WhatsAppAccount


@admin.register(IntegrationAccount)
class IntegrationAccountAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'company',
        'platform',
        'name',
        'status',
        'is_active',
        'created_at',
    ]
    list_filter = ['platform', 'status', 'is_active', 'created_at']
    search_fields = ['name', 'external_account_name', 'company__name']
    readonly_fields = [
        'created_at',
        'updated_at',
        'last_sync_at',
        'token_expires_at',
    ]
    fieldsets = (
        ('معلومات أساسية', {
            'fields': ('company', 'platform', 'name', 'status', 'is_active')
        }),
        ('معلومات الحساب الخارجي', {
            'fields': (
                'external_account_id',
                'external_account_name',
                'account_link',
                'phone_number',
            )
        }),
        ('معلومات OAuth', {
            'fields': (
                'access_token',
                'refresh_token',
                'token_expires_at',
            ),
            'classes': ('collapse',)
        }),
        ('معلومات إضافية', {
            'fields': ('metadata', 'error_message', 'last_sync_at')
        }),
        ('معلومات النظام', {
            'fields': ('created_by', 'created_at', 'updated_at')
        }),
    )


@admin.register(WhatsAppAccount)
class WhatsAppAccountAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'company',
        'display_phone_number',
        'phone_number_id',
        'waba_id',
        'status',
        'created_at',
    ]
    list_filter = ['status', 'company']
    search_fields = ['phone_number_id', 'waba_id', 'company__name', 'display_phone_number']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['company', 'integration_account']


@admin.register(IntegrationLog)
class IntegrationLogAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'account',
        'action',
        'status',
        'created_at',
    ]
    list_filter = ['status', 'action', 'created_at']
    search_fields = ['account__name', 'message']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'

