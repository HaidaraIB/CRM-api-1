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


class InvoiceStatus(Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    CANCELED = "canceled"

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
    users = models.CharField(max_length=50, default="unlimited")
    clients = models.CharField(max_length=50, default="unlimited")
    storage = models.IntegerField(default=10)  # In GB
    visible = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "plans"

    def __str__(self):
        return self.name


class Subscription(models.Model):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name="subscriptions"
    )
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField()
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
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.ForeignKey(
        PaymentGateway, on_delete=models.CASCADE, related_name="payments"
    )
    payment_status = models.CharField(max_length=255, choices=PaymentStatus.choices())
    tran_ref = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments"

    def __str__(self):
        return f"Payment #{self.id} - {self.subscription.company.name} - {self.amount}"


class Invoice(models.Model):
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="invoices"
    )
    invoice_number = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.choices(),
        default=InvoiceStatus.DRAFT.value,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoices"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.subscription.company.name} ({self.status})"


class Broadcast(models.Model):
    subject = models.CharField(max_length=255)
    content = models.TextField()
    target = models.CharField(max_length=50, default="all")
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
