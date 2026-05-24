from django.db import models
from enum import Enum


class Specialization(Enum):
    REAL_ESTATE = "real_estate"
    SERVICES = "services"
    PRODUCTS = "products"
    MEDICAL = "medical"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class Company(models.Model):
    name = models.CharField(max_length=64)
    domain = models.CharField(max_length=256, unique=True)
    specialization = models.CharField(
        max_length=20, choices=Specialization.choices(), default=Specialization.REAL_ESTATE.value
    )
    owner = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="companies"
    )
    last_data_entry_assigned_employee = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="data_entry_round_robin_companies",
        null=True,
        blank=True,
        help_text="Last employee assigned from data-entry round-robin.",
    )
    # Auto assignment settings
    auto_assign_enabled = models.BooleanField(
        default=False,
        help_text="توزيع العملاء على الموظفين حسب النشاط"
    )
    re_assign_enabled = models.BooleanField(
        default=False,
        help_text="تعيين موظف جديد للعميل في حال لم يتواصل معه الموظف الحالي خلال فترة محددة"
    )
    re_assign_hours = models.IntegerField(
        default=24,
        help_text="عدد الساعات قبل إعادة تعيين العميل (افتراضي: 24 ساعة)"
    )
    # After any paid conversion or expired/forfeited trial, user cannot start another time-limited trial.
    free_trial_consumed = models.BooleanField(default=False)
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        help_text="IANA timezone for business calendar (weekly day off, etc.).",
    )
    field_visit_enabled = models.BooleanField(
        default=True,
        help_text="Allow field visits (الزيارة الميدانية) for this company (subject to platform policy).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "companies"

    def __str__(self):
        return self.name


class CompanyPatientCounter(models.Model):
    """
    Per-company monotonic counter for Client.patient_file_number (clinic file #).
    Updated under select_for_update when creating new patients.
    """

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="patient_counter",
    )
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "companies_patient_counter"

    def __str__(self):
        return f"CompanyPatientCounter(company={self.company_id}, next={self.next_number})"


class AdminTenantWhatsAppMessage(models.Model):
    """Platform WhatsApp thread: admin panel ↔ company owner (not CRM client leads)."""

    DIRECTION_INBOUND = "inbound"
    DIRECTION_OUTBOUND = "outbound"
    DIRECTION_CHOICES = (
        (DIRECTION_INBOUND, "Inbound"),
        (DIRECTION_OUTBOUND, "Outbound"),
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="admin_whatsapp_messages",
    )
    direction = models.CharField(max_length=16, choices=DIRECTION_CHOICES)
    body = models.TextField()
    whatsapp_message_id = models.CharField(max_length=128, blank=True, null=True)
    graph_http_status = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "admin_tenant_whatsapp_messages"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.direction} company={self.company_id}"
