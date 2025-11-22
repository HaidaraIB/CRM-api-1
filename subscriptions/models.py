from django.db import models
from django.db.models import JSONField

# Create your models here.


class Plan(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField()
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2)
    trial_days = models.IntegerField(default=0)
    users = models.CharField(max_length=50, default='unlimited')
    clients = models.CharField(max_length=50, default='unlimited')
    storage = models.IntegerField(default=10)  # In GB
    visible = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "plans"


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
    auto_renew = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscriptions"


class Payment(models.Model):
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=255)
    payment_status = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments"


class Invoice(models.Model):
    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('due', 'Due'),
        ('overdue', 'Overdue'),
    ]
    
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="invoices"
    )
    invoice_number = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='due')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoices"
        ordering = ['-created_at']


class Broadcast(models.Model):
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('scheduled', 'Scheduled'),
        ('draft', 'Draft'),
    ]
    
    TARGET_CHOICES = [
        ('all', 'All Companies'),
        ('gold', 'Gold Plan Subscribers'),
        ('trial', 'Trial Accounts'),
        ('expired', 'Expired Subscriptions'),
    ]
    
    subject = models.CharField(max_length=255)
    content = models.TextField()
    target = models.CharField(max_length=50, choices=TARGET_CHOICES, default='all')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "broadcasts"
        ordering = ['-created_at']


class PaymentGateway(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('disabled', 'Disabled'),
        ('setup_required', 'Setup Required'),
    ]
    
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='setup_required')
    enabled = models.BooleanField(default=False)
    config = JSONField(default=dict, blank=True)  # Store gateway-specific config (keys, etc.)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_gateways"
        ordering = ['name']
