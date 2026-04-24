from django.db import models

# Create your models here.
from enum import Enum


class BroadcastStatus(Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"

    @classmethod
    def choices(cls):
        return [(i.value, i.name) for i in cls]


class BroadcastType(Enum):
    EMAIL = "email"
    PUSH = "push"

    @classmethod
    def choices(cls):
        return [(i.value, i.name) for i in cls]


class PaymentStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @classmethod
    def choices(cls):
        return [(i.value, i.name) for i in cls]


class PaymentGatewayStatus(Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    SETUP_REQUIRED = "setup_required"

    @classmethod
    def choices(cls):
        return [(i.value, i.name) for i in cls]


class Plan(models.Model):
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField()
    description_ar = models.TextField(blank=True, default="")
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2)
    trial_days = models.IntegerField(default=0)
    # Higher tier = higher offering; used for upgrade/downgrade (not price heuristics).
    tier = models.IntegerField(
        default=0,
        help_text="Relative plan level; upgrade requires higher tier, downgrade schedules at period end.",
    )
    users = models.CharField(max_length=50, default="unlimited")
    clients = models.CharField(max_length=50, default="unlimited")
    # Entitlements (new): keep legacy fields above for UI compatibility.
    # features: boolean flags (e.g. sms_enabled, whatsapp_enabled)
    # limits: extra quota keys beyond users/clients (e.g. max_deals)
    # usage_limits_monthly: monthly usage caps (e.g. monthly_sms_messages)
    features = models.JSONField(blank=True, default=dict)
    limits = models.JSONField(blank=True, default=dict)
    usage_limits_monthly = models.JSONField(blank=True, default=dict)
    visible = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "plans"

    def __str__(self):
        return self.name


class CompanyUsageCounter(models.Model):
    """
    Monthly usage counters for enforcing plan usage limits.
    Stored in subscriptions app to avoid scattering usage logic across integrations/notifications.
    """

    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="usage_counters"
    )
    key = models.CharField(max_length=64)  # e.g. monthly_sms_messages
    period_start = models.DateField()  # first day of month (UTC)
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_usage_counters"
        unique_together = ("company", "key", "period_start")
        indexes = [
            models.Index(fields=["company", "key", "period_start"]),
        ]

    def __str__(self):
        return f"{self.company_id}:{self.key}:{self.period_start}={self.count}"


class SubscriptionStatus(models.TextChoices):
    TRIALING = "trialing", "Trialing"
    ACTIVE = "active", "Active"
    CANCELED = "canceled", "Canceled"


class BillingCycle(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"


class Subscription(models.Model):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name="subscriptions"
    )
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField()
    # Start of the current paid/trial period (end_date is current period end).
    current_period_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start of current billing period; used for proration.",
    )
    billing_cycle = models.CharField(
        max_length=10,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
    )
    subscription_status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
    )
    # Downgrade / paid→free scheduled for end of current period.
    pending_plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_subscriptions",
    )
    pending_billing_cycle = models.CharField(
        max_length=10,
        choices=BillingCycle.choices,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    auto_renew = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscriptions"

    def __str__(self):
        return f"{self.company.name} - {self.plan.name}"
    
    def is_truly_active(self):
        """
        Check if subscription is truly active (is_active=True AND end_date > now)
        """
        from django.utils import timezone
        return self.is_active and self.end_date > timezone.now()
    
    def days_until_expiry(self):
        """
        Calculate days until subscription expires. Returns negative if expired.
        """
        from django.utils import timezone
        from datetime import timedelta
        delta = self.end_date - timezone.now()
        return delta.days
    
    def is_expiring_soon(self, days_threshold=30):
        """
        Check if subscription is expiring within the threshold (default 30 days)
        """
        days_left = self.days_until_expiry()
        return 0 < days_left <= days_threshold


class PaymentGateway(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=PaymentGatewayStatus.choices(),
        default=PaymentGatewayStatus.SETUP_REQUIRED.value,
    )
    enabled = models.BooleanField(default=False)
    config = models.JSONField(blank=True, default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_gateways"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.status})"


class Payment(models.Model):
    """Amount is stored in the currency the customer actually paid (e.g. IQD or USD)."""
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD", help_text="ISO currency code (USD, IQD, etc.)")
    # Exchange rate at payment time (e.g. USD to IQD). Used to convert to USD for display/reporting.
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True,
        help_text="Rate at payment time (e.g. 1 USD = exchange_rate IQD). Null if USD."
    )
    # Amount in USD for consistent display and reporting. amount_usd = amount / exchange_rate when currency != USD.
    amount_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Payment amount in USD (stored at payment time for reporting)."
    )
    payment_method = models.ForeignKey(
        PaymentGateway, on_delete=models.CASCADE, related_name="payments"
    )
    payment_status = models.CharField(max_length=255, choices=PaymentStatus.choices())
    tran_ref = models.CharField(max_length=255, blank=True, default="")
    # Checkout intent: plan being paid for (may differ from subscription.plan until payment completes).
    target_plan = models.ForeignKey(
        "Plan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_payments",
    )
    billing_cycle = models.CharField(
        max_length=10,
        choices=BillingCycle.choices,
        null=True,
        blank=True,
        help_text="Billing cycle selected for this checkout session.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments"

    def __str__(self):
        return f"Payment #{self.id} - {self.subscription.company.name} - {self.amount} {self.currency}"


class InvoiceSequence(models.Model):
    """Per-year counter for INV-YYYY-NNNNN invoice numbers."""

    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoice_sequences"

    def __str__(self):
        return f"{self.year}: {self.last_number}"


class Invoice(models.Model):
    """
    Read-only billing artifact for SaaS: one invoice per Payment (auto-created).
    Status is always the linked payment's payment_status; legacy rows use legacy_payment_status.
    """

    payment = models.OneToOneField(
        Payment,
        on_delete=models.CASCADE,
        related_name="invoice",
        null=True,
        blank=True,
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="invoices"
    )
    invoice_number = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    company_name = models.CharField(max_length=255, default="")
    plan_name = models.CharField(max_length=255, default="")
    line_description = models.CharField(max_length=512, blank=True, default="")
    billing_cycle = models.CharField(max_length=10, blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    last_emailed_at = models.DateTimeField(null=True, blank=True)
    legacy_payment_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Only for historical invoices without a Payment row; mirrors PaymentStatus.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoices"
        ordering = ["-created_at"]

    def __str__(self):
        ps = (
            self.payment.payment_status
            if self.payment_id
            else (self.legacy_payment_status or "unknown")
        )
        return f"Invoice {self.invoice_number} ({ps})"

    def effective_payment_status(self) -> str:
        if self.payment_id:
            return self.payment.payment_status or ""
        return self.legacy_payment_status or PaymentStatus.COMPLETED.value


class Broadcast(models.Model):
    subject = models.CharField(max_length=255)
    content = models.TextField()
    targets = models.JSONField(default=list, blank=True)  # list of target strings e.g. ["role_admin", "plan_1"]
    broadcast_type = models.CharField(
        max_length=10,
        choices=BroadcastType.choices(),
        default=BroadcastType.EMAIL.value
    )
    status = models.CharField(
        max_length=20, choices=BroadcastStatus.choices(), null=True
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcasts"
        ordering = ["-created_at"]

    def __str__(self):
        status_display = self.status or "draft"
        return f"{self.subject} ({status_display})"
