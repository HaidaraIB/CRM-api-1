from django.db import models
from enum import Enum

# Create your models here.


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class Type(Enum):
    FRESH = "fresh"
    COLD = "cold"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class PhoneNumberType(Enum):
    MOBILE = "mobile"
    HOME = "home"
    WORK = "work"
    OTHER = "other"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class DealStage(Enum):
    WON = "won"
    LOST = "lost"
    ON_HOLD = "on_hold"
    IN_PROGRESS = "in_progress"
    CANCELLED = "cancelled"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class Client(models.Model):
    name = models.CharField(max_length=255)
    priority = models.CharField(max_length=10, choices=Priority.choices())
    type = models.CharField(max_length=20, choices=Type.choices())
    # Link to Channel from settings instead of enum
    communication_way = models.ForeignKey(
        "settings.Channel",
        on_delete=models.SET_NULL,
        related_name="clients",
        blank=True,
        null=True,
        help_text="Communication channel for this client",
    )
    # Link to LeadStatus from settings instead of enum
    status = models.ForeignKey(
        "settings.LeadStatus",
        on_delete=models.SET_NULL,
        related_name="clients",
        blank=True,
        null=True,
        help_text="Current status of this client",
    )

    budget = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)  # Keep for backward compatibility

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="clients",
    )
    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="assigned_clients",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ClientPhoneNumber(models.Model):
    """Model to store multiple phone numbers for a client"""
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="phone_numbers",
        help_text="The client this phone number belongs to",
    )
    phone_number = models.CharField(
        max_length=20,
        help_text="The phone number",
    )
    phone_type = models.CharField(
        max_length=10,
        choices=PhoneNumberType.choices(),
        default=PhoneNumberType.MOBILE.value,
        help_text="Type of phone number (mobile, home, work, other)",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Whether this is the primary phone number",
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text="Additional notes about this phone number",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "client_phone_numbers"
        ordering = ["-is_primary", "phone_type", "created_at"]
        unique_together = [["client", "phone_number"]]

    def __str__(self):
        return f"{self.client.name} - {self.phone_number} ({self.phone_type})"


class Deal(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="deals")
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="deals"
    )
    employee = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="deals",
        blank=True,
        null=True,
    )
    stage = models.CharField(max_length=50, choices=DealStage.choices())
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.client.name} - {self.stage}"


class Task(models.Model):
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="tasks")
    # Link to LeadStage from settings instead of enum
    stage = models.ForeignKey(
        "settings.LeadStage",
        on_delete=models.SET_NULL,
        related_name="tasks",
        blank=True,
        null=True,
        help_text="Current stage of this task",
    )
    notes = models.TextField(blank=True, null=True)
    reminder_date = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        stage_name = self.stage.name if self.stage else "No Stage"
        return f"{self.deal.client.name} - {stage_name}"


class ClientTask(models.Model):
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="client_tasks"
    )
    # Link to LeadStage from settings instead of enum
    stage = models.ForeignKey(
        "settings.LeadStage",
        on_delete=models.SET_NULL,
        related_name="client_tasks",
        blank=True,
        null=True,
        help_text="Current stage of this client task",
    )
    notes = models.TextField(blank=True, null=True)
    reminder_date = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="created_client_tasks",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_client_task"
        ordering = ["-created_at"]

    def __str__(self):
        stage_name = self.stage.name if self.stage else "No Stage"
        return f"{self.client.name} - {stage_name}"


class Campaign(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="campaigns"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
