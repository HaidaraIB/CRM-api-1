from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
import json

User = get_user_model()


class NotificationType(models.TextChoices):
    # Core Notifications
    NEW_LEAD = 'new_lead', 'New Lead'
    LEAD_NO_FOLLOW_UP = 'lead_no_follow_up', 'Lead No Follow Up'
    LEAD_REENGAGED = 'lead_reengaged', 'Lead Reengaged'
    LEAD_CONTACT_FAILED = 'lead_contact_failed', 'Lead Contact Failed'
    LEAD_STATUS_CHANGED = 'lead_status_changed', 'Lead Status Changed'
    LEAD_ASSIGNED = 'lead_assigned', 'Lead Assigned'
    LEAD_TRANSFERRED = 'lead_transferred', 'Lead Transferred'
    LEAD_UPDATED = 'lead_updated', 'Lead Updated'
    LEAD_REMINDER = 'lead_reminder', 'Lead Reminder'
    
    # WhatsApp Notifications
    WHATSAPP_MESSAGE_RECEIVED = 'whatsapp_message_received', 'WhatsApp Message Received'
    WHATSAPP_TEMPLATE_SENT = 'whatsapp_template_sent', 'WhatsApp Template Sent'
    WHATSAPP_SEND_FAILED = 'whatsapp_send_failed', 'WhatsApp Send Failed'
    WHATSAPP_WAITING_RESPONSE = 'whatsapp_waiting_response', 'WhatsApp Waiting Response'
    
    # Campaign Notifications
    CAMPAIGN_PERFORMANCE = 'campaign_performance', 'Campaign Performance'
    CAMPAIGN_LOW_PERFORMANCE = 'campaign_low_performance', 'Campaign Low Performance'
    CAMPAIGN_STOPPED = 'campaign_stopped', 'Campaign Stopped'
    CAMPAIGN_BUDGET_ALERT = 'campaign_budget_alert', 'Campaign Budget Alert'
    
    # Team & Tasks
    TASK_CREATED = 'task_created', 'Task Created'
    TASK_REMINDER = 'task_reminder', 'Task Reminder'
    TASK_COMPLETED = 'task_completed', 'Task Completed'
    
    # Deals
    DEAL_CREATED = 'deal_created', 'Deal Created'
    DEAL_UPDATED = 'deal_updated', 'Deal Updated'
    DEAL_CLOSED = 'deal_closed', 'Deal Closed'
    DEAL_REMINDER = 'deal_reminder', 'Deal Reminder'
    
    # Reports
    DAILY_REPORT = 'daily_report', 'Daily Report'
    WEEKLY_REPORT = 'weekly_report', 'Weekly Report'
    TOP_EMPLOYEE = 'top_employee', 'Top Employee'
    
    # System & Subscription
    LOGIN_FROM_NEW_DEVICE = 'login_from_new_device', 'Login from New Device'
    SYSTEM_UPDATE = 'system_update', 'System Update'
    SUBSCRIPTION_EXPIRING = 'subscription_expiring', 'Subscription Expiring'
    PAYMENT_FAILED = 'payment_failed', 'Payment Failed'
    SUBSCRIPTION_EXPIRED = 'subscription_expired', 'Subscription Expired'
    
    # General
    GENERAL = 'general', 'General'


class Notification(models.Model):
    """Model to store sent notifications"""
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
        default=NotificationType.GENERAL
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    data = models.JSONField(default=dict, blank=True)
    image_url = models.URLField(blank=True, null=True)
    read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['user', 'read']),
            models.Index(fields=['type']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.title}"
    
    def mark_as_read(self):
        """Mark notification as read"""
        if not self.read:
            self.read = True
            self.read_at = timezone.now()
            self.save(update_fields=['read', 'read_at'])
    
    def get_type_display(self):
        """Get human-readable type name"""
        return dict(NotificationType.choices).get(self.type, self.type)


class NotificationSettings(models.Model):
    """Model to store user notification preferences"""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='notification_settings'
    )
    
    # Global enable/disable
    enabled = models.BooleanField(default=True, help_text="Enable/disable all notifications")
    
    # Per-type settings (JSON field storing {notification_type: enabled})
    # Example: {"new_lead": true, "lead_no_follow_up": false}
    notification_types = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dictionary of notification types and their enabled status"
    )
    
    # Time restrictions
    restrict_time = models.BooleanField(
        default=False,
        help_text="Restrict notifications to specific time periods"
    )
    start_hour = models.IntegerField(
        default=9,
        help_text="Start hour for notifications (0-23)"
    )
    end_hour = models.IntegerField(
        default=18,
        help_text="End hour for notifications (0-23)"
    )
    enabled_days = models.JSONField(
        default=list,
        blank=True,
        help_text="List of enabled days (0=Sunday, 6=Saturday). Default: all days"
    )
    
    # Source settings (for lead source filtering)
    source_settings = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dictionary of lead sources and their enabled status"
    )
    
    # User role settings (for filtering by user role)
    user_role_settings = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dictionary of user roles and their enabled status"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'notification_settings'
        verbose_name = 'Notification Settings'
        verbose_name_plural = 'Notification Settings'
    
    def __str__(self):
        return f"Notification Settings for {self.user.username}"
    
    @classmethod
    def get_or_create_for_user(cls, user):
        """Get or create notification settings for a user"""
        settings, created = cls.objects.get_or_create(
            user=user,
            defaults={
                'enabled': True,
                'notification_types': {},
                'enabled_days': [True] * 7,  # All days enabled by default
            }
        )
        return settings
    
    def _camel_to_snake(self, name):
        """Convert camelCase to snake_case"""
        import re
        # Insert an underscore before any uppercase letter followed by lowercase
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        # Insert an underscore before any uppercase letter that follows a lowercase letter or digit
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    
    def _snake_to_camel(self, name):
        """Convert snake_case to camelCase"""
        components = name.split('_')
        return components[0] + ''.join(x.capitalize() for x in components[1:])
    
    def is_notification_enabled(self, notification_type: str) -> bool:
        """
        Check if a specific notification type is enabled for this user
        
        Args:
            notification_type: The notification type to check (in snake_case format)
            
        Returns:
            True if notification should be sent, False otherwise
        """
        # Check global enable/disable
        if not self.enabled:
            return False
        
        # Check per-type settings (if exists, use it; otherwise default to True)
        # IMPORTANT: If notification_types is empty {}, it means settings haven't been synced
        # from mobile app yet, so we default to enabled. Once synced, we respect the explicit settings.
        if self.notification_types:
            # Try snake_case first (Django format)
            type_enabled = self.notification_types.get(notification_type)
            
            # If not found, try camelCase (Flutter format) - for backward compatibility
            if type_enabled is None:
                camel_case_key = self._snake_to_camel(notification_type)
                type_enabled = self.notification_types.get(camel_case_key)
            
            if type_enabled is not None:  # Explicitly set
                return type_enabled
        
        # Default to enabled if not explicitly set
        # This happens when:
        # 1. Settings haven't been synced from mobile app (notification_types is empty {})
        # 2. User hasn't explicitly disabled this type
        return True
    
    def can_send_now(self) -> bool:
        """
        Check if notifications can be sent at the current time
        
        Returns:
            True if current time is within allowed hours and days
        """
        if not self.restrict_time:
            return True
        
        now = timezone.now()
        current_hour = now.hour
        current_day = now.weekday()  # 0=Monday, 6=Sunday
        
        # Adjust for Django's weekday (Monday=0) vs our format (Sunday=0)
        # Convert: Monday=0 -> Sunday=6, Tuesday=1 -> Monday=0, etc.
        day_index = (current_day + 1) % 7
        
        # Check if current day is enabled
        enabled_days = self.enabled_days if self.enabled_days else [True] * 7
        if day_index >= len(enabled_days) or not enabled_days[day_index]:
            return False
        
        # Check if current hour is within allowed range
        start_hour = self.start_hour
        end_hour = self.end_hour
        
        if start_hour <= end_hour:
            # Normal range (e.g., 9:00 - 18:00)
            return start_hour <= current_hour < end_hour
        else:
            # Range spans midnight (e.g., 22:00 - 06:00)
            return current_hour >= start_hour or current_hour < end_hour
    
    def should_send_notification(
        self,
        notification_type: str,
        lead_source: str = None,
        sender_role: str = None
    ) -> bool:
        """
        Comprehensive check if notification should be sent
        
        Args:
            notification_type: The notification type
            lead_source: Optional lead source (for source filtering)
            sender_role: Optional sender role (for role filtering)
            
        Returns:
            True if notification should be sent, False otherwise
        """
        # Check global enable
        if not self.enabled:
            return False
        
        # Check notification type
        if not self.is_notification_enabled(notification_type):
            return False
        
        # Check time restrictions
        if not self.can_send_now():
            return False
        
        # Check source settings (if lead_source provided)
        if lead_source and self.source_settings:
            source_enabled = self.source_settings.get(lead_source)
            if source_enabled is not None and not source_enabled:
                return False
        
        # Check role settings (if sender_role provided)
        if sender_role and self.user_role_settings:
            role_enabled = self.user_role_settings.get(sender_role)
            if role_enabled is not None and not role_enabled:
                return False
        
        return True
