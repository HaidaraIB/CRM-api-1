from django.db import models
from django.conf import settings as django_settings
from django.utils import timezone
from enum import Enum
import uuid


class ChannelPriority(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class Channel(models.Model):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    priority = models.CharField(
        max_length=10,
        choices=ChannelPriority.choices(),
        default=ChannelPriority.MEDIUM.value,
    )
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="channels"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ["name", "company"]

    def __str__(self):
        return self.name


class LeadStage(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default="#808080")  # Hex color
    required = models.BooleanField(default=False)
    auto_advance = models.BooleanField(default=False)
    order = models.IntegerField(default=0)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="lead_stages"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_lead_stage"
        ordering = ["order", "name"]
        unique_together = ["name", "company"]

    def __str__(self):
        return self.name


class StatusCategory(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FOLLOW_UP = "follow_up"
    CLOSED = "closed"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class LeadStatus(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(
        max_length=20,
        choices=StatusCategory.choices(),
        default=StatusCategory.ACTIVE.value,
    )
    color = models.CharField(max_length=7, default="#808080")  # Hex color
    is_default = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="lead_statuses"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_lead_status"
        ordering = ["-is_default", "name"]
        unique_together = ["name", "company"]

    def __str__(self):
        return self.name


class SMTPSettings(models.Model):
    """
    SMTP configuration for sending emails.
    Only one instance should exist (singleton pattern).
    """

    host = models.CharField(
        max_length=255, help_text="SMTP server host (e.g., smtp.gmail.com)"
    )
    port = models.IntegerField(
        default=587,
        help_text="SMTP server port (587 for TLS, 465 for SSL, 25 for plain)",
    )
    use_tls = models.BooleanField(default=True, help_text="Use TLS encryption")
    use_ssl = models.BooleanField(default=False, help_text="Use SSL encryption")
    username = models.CharField(
        max_length=255, help_text="SMTP username (usually email address)"
    )
    password = models.CharField(
        max_length=255, help_text="SMTP password (leave empty to keep current)"
    )
    from_email = models.EmailField(help_text="Default 'from' email address")
    from_name = models.CharField(
        max_length=255, blank=True, help_text="Default 'from' name"
    )
    is_active = models.BooleanField(default=False, help_text="Enable/disable SMTP")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_smtp_settings"
        verbose_name = "SMTP Settings"
        verbose_name_plural = "SMTP Settings"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"SMTP: {self.host}:{self.port}"

    @classmethod
    def get_settings(cls):
        """Get the SMTP settings instance (singleton)"""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings


class SystemBackup(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Initiator(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to="backups/", blank=True, null=True)
    file_size = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.IN_PROGRESS
    )
    initiator = models.CharField(
        max_length=20, choices=Initiator.choices, default=Initiator.MANUAL
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_backups",
    )
    notes = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "settings_system_backup"
        ordering = ["-created_at"]
        verbose_name = "System Backup"
        verbose_name_plural = "System Backups"

    def mark_completed(self, file_size: int = 0, metadata=None):
        self.status = self.Status.COMPLETED
        self.completed_at = timezone.now()
        if file_size:
            self.file_size = file_size
        if metadata:
            current = self.metadata or {}
            current.update(metadata)
            self.metadata = current
        self.save(
            update_fields=["status", "completed_at", "file_size", "metadata"]
        )

    def mark_failed(self, error_message: str):
        self.status = self.Status.FAILED
        self.error_message = error_message
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "error_message", "completed_at"])


class SystemAuditLog(models.Model):
    """Persistent audit log for sensitive system actions."""

    id = models.BigAutoField(primary_key=True)
    action = models.CharField(max_length=128)
    message = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(blank=True, default=dict)
    actor = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="system_audit_events",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "settings_system_audit_log"
        ordering = ["-created_at"]
        verbose_name = "System Audit Log"
        verbose_name_plural = "System Audit Logs"

    def __str__(self):
        return f"{self.action} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class CallMethod(models.Model):
    """Model for call methods (similar to LeadStage and LeadStatus)"""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default="#808080")  # Hex color
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="call_methods"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_call_method"
        ordering = ["name"]
        unique_together = ["name", "company"]

    def __str__(self):
        return self.name


class SystemSettings(models.Model):
    """
    System-wide settings configuration.
    Only one instance should exist (singleton pattern).
    """
    BACKUP_SCHEDULE_CHOICES = [
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
    ]

    usd_to_iqd_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1300.00,
        help_text="USD to IQD conversion rate"
    )
    backup_schedule = models.CharField(
        max_length=20,
        choices=BACKUP_SCHEDULE_CHOICES,
        default="daily",
        help_text="Backup schedule: daily, weekly, or monthly",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_system_settings"
        verbose_name = "System Settings"
        verbose_name_plural = "System Settings"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"System Settings (USD to IQD: {self.usd_to_iqd_rate})"

    @classmethod
    def get_settings(cls):
        """Get the system settings instance (singleton)"""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings