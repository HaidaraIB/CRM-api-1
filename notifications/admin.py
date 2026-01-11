from django.contrib import admin
from .models import Notification


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
