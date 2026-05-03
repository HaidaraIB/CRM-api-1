from django.db import models
from django.utils import timezone
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
    budget_max = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Optional upper bound when budget is a range; null means single value (budget only).",
    )
    phone_number = models.CharField(
        max_length=20, blank=True, null=True
    )  # Keep for backward compatibility

    lead_company_name = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="اسم شركة العميل / الليد (اختياري)",
    )
    profession = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="المهنة (اختياري)",
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text="Optional free-form notes on this lead (not activity/task notes).",
    )
    interested_developer = models.ForeignKey(
        "real_estate.Developer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interested_leads",
        help_text="Optional developer the lead is interested in (real estate).",
    )
    interested_project = models.ForeignKey(
        "real_estate.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interested_leads",
        help_text="Optional project the lead is interested in (real estate).",
    )
    interested_unit = models.ForeignKey(
        "real_estate.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interested_leads",
        help_text="Optional unit the lead is interested in (real estate).",
    )

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
    status_entered_at = models.DateTimeField(
        default=timezone.now,
        help_text="When the lead entered the current status (used for stale-status auto-delete).",
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

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_clients",
        help_text="CRM user who created this lead; null for Meta/TikTok/WhatsApp or legacy rows",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "assigned_to"], name="idx_client_company_assigned"),
            models.Index(fields=["company", "created_at"], name="idx_client_company_created"),
            models.Index(fields=["company", "status"], name="idx_client_company_status"),
            models.Index(
                fields=["company", "status", "status_entered_at"],
                name="idx_client_co_stat_entered",
            ),
            models.Index(fields=["company", "source"], name="idx_client_company_source"),
        ]

    def save(self, *args, **kwargs):
        """
        Enforce weekly day off on assignee changes. bulk_update() bypasses save(); use normal
        save(update_fields=[...,'assigned_to',...]) for bulk assign so this always runs.
        """
        skip = kwargs.pop("_skip_assignee_availability_check", False)
        update_fields = kwargs.get("update_fields")

        if (
            not skip
            and self.assigned_to_id
            and (update_fields is None or "assigned_to" in update_fields)
        ):
            from django.core.exceptions import ValidationError

            from crm.availability import user_accepts_new_assignments

            old_aid = None
            if self.pk:
                old_aid = (
                    type(self)
                    .objects.filter(pk=self.pk)
                    .values_list("assigned_to_id", flat=True)
                    .first()
                )
            if old_aid != self.assigned_to_id:
                assignee = self.assigned_to
                cal = getattr(self, "company", None)
                if cal is None and self.company_id:
                    from companies.models import Company

                    cal = Company.objects.filter(pk=self.company_id).first()
                cal = cal or getattr(assignee, "company", None)
                if not user_accepts_new_assignments(
                    assignee, company_for_calendar=cal
                ):
                    raise ValidationError(
                        {
                            "assigned_to": "Cannot assign to this user on their weekly day off.",
                            "error_key": "employee_weekly_day_off",
                        }
                    )

        super().save(*args, **kwargs)

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
        constraints = [
            models.UniqueConstraint(
                fields=["client", "phone_number"],
                name="unique_client_phone_number"
            )
        ]

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
    reminder_date = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Follow-up reminder datetime for this deal",
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

    class Meta:
        indexes = [
            models.Index(fields=["company", "employee"], name="idx_deal_company_employee"),
            models.Index(fields=["company", "stage"], name="idx_deal_company_stage"),
            models.Index(fields=["company", "created_at"], name="idx_deal_company_created"),
        ]

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
    call_datetime = models.DateTimeField(blank=True, null=True, help_text="Date and time when the call happened")
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


class ClientVisit(models.Model):
    """Site / office visit log for real_estate and services companies."""

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="client_visits"
    )
    visit_type = models.ForeignKey(
        "settings.VisitType",
        on_delete=models.SET_NULL,
        related_name="client_visits",
        blank=True,
        null=True,
        help_text="Visit type from company settings",
    )
    summary = models.TextField(blank=True, null=True)
    visit_datetime = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When the visit occurred",
    )
    upcoming_visit_date = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Optional scheduled next visit",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="created_client_visits",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_client_visit"
        ordering = ["-created_at"]

    def __str__(self):
        vt = self.visit_type.name if self.visit_type else "No type"
        return f"{self.client.name} - {vt}"


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
