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
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]
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
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_lead_stage"
        ordering = ["-is_default", "order", "name"]
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
    automation_key = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Reserved key for system automation (e.g. visited). Display name may differ.",
    )
    auto_delete_after_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="If set, leads in this status longer than this many hours are deleted by a scheduled job.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_lead_status"
        ordering = ["-is_default", "name"]
        unique_together = ["name", "company"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "automation_key"],
                condition=models.Q(automation_key__isnull=False),
                name="uniq_leadstatus_company_automation_key_when_set",
            ),
        ]

    def __str__(self):
        return self.name


class SMTPSettings(models.Model):
    """
    Platform outbound email settings (Resend). Only one row (singleton).

    Sending uses the Resend HTTP API; set RESEND_API_KEY in the server environment.
    Host/port/username/password are legacy DB columns and are not used for transport.
    """

    host = models.CharField(
        max_length=255,
        help_text="Legacy field (unused). Resend is configured via RESEND_API_KEY.",
    )
    port = models.IntegerField(
        default=587,
        help_text="Legacy field (unused).",
    )
    use_tls = models.BooleanField(default=True, help_text="Legacy field (unused).")
    use_ssl = models.BooleanField(default=False, help_text="Legacy field (unused).")
    username = models.CharField(
        max_length=255,
        help_text="Legacy field (unused).",
    )
    password = models.CharField(
        max_length=255,
        help_text="Legacy field (unused).",
    )
    from_email = models.EmailField(
        help_text="Default 'from' address (must match a domain verified in Resend)"
    )
    from_name = models.CharField(
        max_length=255, blank=True, help_text="Default 'from' display name"
    )
    is_active = models.BooleanField(
        default=False, help_text="Enable/disable outbound email (Resend)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_smtp_settings"
        verbose_name = "Outbound email (Resend)"
        verbose_name_plural = "Outbound email (Resend)"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Email: {self.from_email} (active={self.is_active})"

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
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_call_method"
        ordering = ["-is_default", "name"]
        unique_together = ["name", "company"]

    def __str__(self):
        return self.name


class VisitType(models.Model):
    """Configurable visit categories for real_estate / services (parallel to CallMethod)."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default="#808080")
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="visit_types"
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_visit_type"
        ordering = ["-is_default", "name"]
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

    # Mobile app minimum versions (semver x.y.z). Empty = do not enforce for that OS.
    mobile_minimum_version_android = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Minimum Android app version (e.g. 1.2.1). Empty disables enforcement.",
    )
    mobile_minimum_version_ios = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Minimum iOS app version (e.g. 1.2.1). Empty disables enforcement.",
    )
    mobile_minimum_build_android = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional: when installed version equals minimum, require at least this build (Android versionCode).",
    )
    mobile_minimum_build_ios = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional: when installed version equals minimum, require at least this build (iOS CFBundleVersion).",
    )
    mobile_store_url_android = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Play Store URL or market:// link for forced update.",
    )
    mobile_store_url_ios = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="App Store URL for forced update.",
    )
    integration_policies = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Integration gating policies by platform. "
            "Schema: {platform: {global_enabled, global_message, company_overrides{company_id:{enabled,message}}}}"
        ),
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


class PlatformTwilioSettings(models.Model):
    """
    Platform-level Twilio settings for admin SMS broadcast.
    Singleton pattern - only one instance (pk=1). Used by Communication > Send SMS.
    """
    account_sid = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Twilio Account SID",
    )
    twilio_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Twilio sender number",
    )
    auth_token = models.TextField(
        blank=True,
        null=True,
        help_text="Auth Token (stored encrypted)",
    )
    sender_id = models.CharField(
        max_length=11,
        blank=True,
        null=True,
        help_text="Sender ID (optional, alphanumeric)",
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text="Enable platform SMS broadcast",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_platform_twilio_settings"
        verbose_name = "Platform Twilio Settings"
        verbose_name_plural = "Platform Twilio Settings"
        ordering = ["-updated_at"]

    def __str__(self):
        return "Platform Twilio (SMS Broadcast)"

    def get_auth_token(self):
        if not self.auth_token:
            return None
        from integrations.encryption import decrypt_token
        return decrypt_token(self.auth_token)

    def set_auth_token(self, token):
        if token:
            from integrations.encryption import encrypt_token
            self.auth_token = encrypt_token(token)
        else:
            self.auth_token = None

    @classmethod
    def get_settings(cls):
        """Get the singleton instance."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PlatformWhatsAppSettings(models.Model):
    """
    Platform-level WhatsApp Cloud API (signup OTP + admin → tenant owner).
    Singleton (pk=1). Non-empty DB fields override django.conf env defaults.
    """

    phone_number_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="WhatsApp Cloud API phone_number_id",
    )
    access_token = models.TextField(
        blank=True,
        null=True,
        help_text="Permanent access token (stored encrypted)",
    )
    graph_api_version = models.CharField(
        max_length=16,
        blank=True,
        null=True,
        default="v25.0",
        help_text="Graph API version, e.g. v25.0",
    )
    otp_template_name = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="Approved authentication template name for OTP",
    )
    otp_template_lang = models.CharField(
        max_length=16,
        blank=True,
        null=True,
        default="en",
        help_text="Template language code",
    )
    admin_template_name = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="Optional utility template for admin outbound messages",
    )
    admin_template_lang = models.CharField(
        max_length=16,
        blank=True,
        null=True,
        default="en",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_platform_whatsapp_settings"
        verbose_name = "Platform WhatsApp Settings"
        verbose_name_plural = "Platform WhatsApp Settings"
        ordering = ["-updated_at"]

    def __str__(self):
        return "Platform WhatsApp (Cloud API)"

    def get_access_token(self):
        if not self.access_token:
            return None
        from integrations.encryption import decrypt_token

        return decrypt_token(self.access_token)

    def set_access_token(self, token):
        if token:
            from integrations.encryption import encrypt_token

            self.access_token = encrypt_token(token)
        else:
            self.access_token = None

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class BillingSettings(models.Model):
    """
    Platform issuer details and logo for SaaS subscription invoices (PDF / email).
    Singleton (pk=1).
    """

    issuer_name = models.CharField(max_length=255, blank=True, default="")
    issuer_address = models.TextField(blank=True, default="")
    issuer_email = models.EmailField(blank=True, default="")
    issuer_phone = models.CharField(max_length=64, blank=True, default="")
    issuer_tax_id = models.CharField(max_length=64, blank=True, default="")
    footer_text = models.TextField(blank=True, default="")
    payment_instructions = models.TextField(blank=True, default="")
    logo = models.ImageField(upload_to="billing/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings_billing_settings"
        verbose_name = "Billing settings (invoices)"
        verbose_name_plural = "Billing settings (invoices)"

    def __str__(self):
        return "Billing settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj