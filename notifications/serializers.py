from rest_framework import serializers
from .models import Notification, NotificationType, NotificationSettings


class NotificationSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    
    class Meta:
        model = Notification
        fields = [
            'id',
            'type',
            'type_display',
            'title',
            'body',
            'data',
            'image_url',
            'read',
            'read_at',
            'sent_at',
            'created_at',
        ]
        read_only_fields = ['sent_at', 'created_at', 'read_at']


class NotificationListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    
    class Meta:
        model = Notification
        fields = [
            'id',
            'type',
            'type_display',
            'title',
            'body',
            'read',
            'sent_at',
            'created_at',
        ]


class NotificationSettingsSerializer(serializers.ModelSerializer):
    """Serializer for NotificationSettings model"""
    
    class Meta:
        model = NotificationSettings
        fields = [
            'enabled',
            'notification_types',
            'restrict_time',
            'start_hour',
            'end_hour',
            'enabled_days',
            'source_settings',
            'user_role_settings',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']
