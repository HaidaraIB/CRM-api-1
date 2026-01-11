from django.contrib import admin
from .models import Notification, NotificationSettings


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'type', 'title', 'read', 'sent_at', 'created_at']
    list_filter = ['type', 'read', 'sent_at']
    search_fields = ['user__username', 'user__email', 'title', 'body']
    readonly_fields = ['sent_at', 'created_at', 'read_at']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('user', 'type', 'title', 'body')
        }),
        ('Additional Data', {
            'fields': ('data', 'image_url')
        }),
        ('Status', {
            'fields': ('read', 'read_at', 'sent_at', 'created_at')
        }),
    )


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ['user', 'enabled', 'restrict_time', 'start_hour', 'end_hour', 'updated_at']
    list_filter = ['enabled', 'restrict_time']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Global Settings', {
            'fields': ('enabled', 'notification_types')
        }),
        ('Time Restrictions', {
            'fields': ('restrict_time', 'start_hour', 'end_hour', 'enabled_days')
        }),
        ('Filter Settings', {
            'fields': ('source_settings', 'user_role_settings')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
