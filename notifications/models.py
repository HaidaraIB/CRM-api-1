from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

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
