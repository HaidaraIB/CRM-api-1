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


class DealStatus(Enum):
    RESERVATION = "reservation"
    CONTRACTED = "contracted"
    CLOSED = "closed"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class DealPaymentMethod(Enum):
    CASH = "cash"
    INSTALLMENT = "installment"

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
    phone_number = models.CharField(
        max_length=20, blank=True, null=True
    )  # Keep for backward compatibility

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
    assigned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="تاريخ ووقت تعيين العميل للموظف"
    )
    last_contacted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="تاريخ ووقت آخر تواصل مع العميل"
    )

    # Integration fields
    campaign = models.ForeignKey(
        "crm.Campaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients",
        help_text="الحملة الإعلانية المرتبطة بهذا العميل"
    )
    source = models.CharField(
        max_length=50,
        choices=[
            ('meta_lead_form', 'Meta Lead Form'),
            ('whatsapp', 'WhatsApp'),
            ('tiktok', 'TikTok'),
            ('manual', 'Manual'),
            ('other', 'Other'),
        ],
        default='manual',
        help_text="مصدر الليد"
    )
    integration_account = models.ForeignKey(
        "integrations.IntegrationAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients",
        help_text="حساب التكامل المرتبط بهذا العميل"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ClientEvent(models.Model):
    """Model to store events related to a client (status changes, assignments, etc.)"""
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="events",
        help_text="The client this event belongs to"
    )
    event_type = models.CharField(
        max_length=50,
        help_text="Type of event (status_change, assignment, edit, etc.)"
    )
    old_value = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Old value before the change"
    )
    new_value = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="New value after the change"
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text="Additional details about the event"
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="created_client_events",
        blank=True,
        null=True,
        help_text="User who triggered the event"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_client_event"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client.name} - {self.event_type} - {self.created_at}"


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
    stage = models.CharField(
        max_length=50, choices=DealStage.choices(), default=DealStage.IN_PROGRESS.value
    )
    # Additional fields for deal details
    payment_method = models.CharField(
        max_length=50,
        choices=DealPaymentMethod.choices(),
        default=DealPaymentMethod.CASH.value,
        help_text="Payment method (Cash, Installment, etc.)",
    )
    status = models.CharField(
        max_length=50,
        choices=DealStatus.choices(),
        default=DealStatus.RESERVATION.value,
        help_text="Deal status (Reservation, Contracted, Closed, etc.)",
    )
    value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Total deal value",
    )
    start_date = models.DateField(blank=True, null=True, help_text="Deal start date")
    closed_date = models.DateField(blank=True, null=True, help_text="Deal closed date")
    discount_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, help_text="Discount percentage"
    )
    discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, help_text="Discount amount"
    )
    sales_commission_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Sales commission percentage",
    )
    sales_commission_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, help_text="Sales commission amount"
    )
    description = models.TextField(
        blank=True, null=True, help_text="Deal description/notes"
    )
    # Real estate specific fields
    unit = models.ForeignKey(
        "real_estate.Unit",
        on_delete=models.SET_NULL,
        related_name="deals",
        blank=True,
        null=True,
        help_text="Unit for real estate deals",
    )
    project = models.ForeignKey(
        "real_estate.Project",
        on_delete=models.SET_NULL,
        related_name="deals",
        blank=True,
        null=True,
        help_text="Project for real estate deals",
    )
    # User tracking
    started_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="started_deals",
        blank=True,
        null=True,
        help_text="User who started the deal",
    )
    closed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="closed_deals",
        blank=True,
        null=True,
        help_text="User who closed the deal",
    )
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


class ClientCall(models.Model):
    """Model for client calls (similar to ClientTask but for calls)"""
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="client_calls"
    )
    # Link to CallMethod from settings
    call_method = models.ForeignKey(
        "settings.CallMethod",
        on_delete=models.SET_NULL,
        related_name="client_calls",
        blank=True,
        null=True,
        help_text="Call method for this call",
    )
    notes = models.TextField(blank=True, null=True)
    follow_up_date = models.DateTimeField(blank=True, null=True, help_text="Next call date or follow up date")
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="created_client_calls",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_client_call"
        ordering = ["-created_at"]

    def __str__(self):
        call_method_name = self.call_method.name if self.call_method else "No Method"
        return f"{self.client.name} - {call_method_name}"


class Campaign(models.Model):
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="campaigns"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_campaign_code_per_company')
        ]

    def __str__(self):
        return self.name
