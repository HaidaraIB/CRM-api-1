from django.db import models
from enum import Enum


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
    priority = models.CharField(max_length=10, choices=ChannelPriority.choices(), default=ChannelPriority.MEDIUM.value)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="channels"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['name', 'company']

    def __str__(self):
        return self.name


class LeadStage(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#808080')  # Hex color
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
        ordering = ['order', 'name']
        unique_together = ['name', 'company']

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
    category = models.CharField(max_length=20, choices=StatusCategory.choices(), default=StatusCategory.ACTIVE.value)
    color = models.CharField(max_length=7, default='#808080')  # Hex color
    is_default = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="lead_statuses"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', 'name']
        unique_together = ['name', 'company']

    def __str__(self):
        return self.name


class SMTPSettings(models.Model):
    """
    SMTP configuration for sending emails.
    Only one instance should exist (singleton pattern).
    """
    host = models.CharField(max_length=255, help_text="SMTP server host (e.g., smtp.gmail.com)")
    port = models.IntegerField(default=587, help_text="SMTP server port (587 for TLS, 465 for SSL, 25 for plain)")
    use_tls = models.BooleanField(default=True, help_text="Use TLS encryption")
    use_ssl = models.BooleanField(default=False, help_text="Use SSL encryption")
    username = models.CharField(max_length=255, help_text="SMTP username (usually email address)")
    password = models.CharField(max_length=255, help_text="SMTP password (leave empty to keep current)")
    from_email = models.EmailField(help_text="Default 'from' email address")
    from_name = models.CharField(max_length=255, blank=True, help_text="Default 'from' name")
    is_active = models.BooleanField(default=False, help_text="Enable/disable SMTP")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMTP Settings"
        verbose_name_plural = "SMTP Settings"
        ordering = ['-updated_at']

    def __str__(self):
        return f"SMTP: {self.host}:{self.port}"

    def save(self, *args, **kwargs):
        # If password is empty, keep the existing password
        if not self.password and self.pk:
            existing = SMTPSettings.objects.get(pk=self.pk)
            self.password = existing.password
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """Get the SMTP settings instance (singleton)"""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings


