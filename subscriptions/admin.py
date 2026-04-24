from django.contrib import admin
from .models import Plan, Subscription, Payment, Invoice, InvoiceSequence, Broadcast, PaymentGateway


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Admin configuration for Plan model"""

    list_display = [
        "name",
        "name_ar",
        "tier",
        "price_monthly",
        "price_yearly",
        "trial_days",
        "visible",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "visible",
        "tier",
        "created_at",
        "updated_at",
    ]
    search_fields = [
        "name",
        "name_ar",
        "description",
        "description_ar",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Plan Information",
            {
                "fields": (
                    "name",
                    "name_ar",
                    "description",
                    "description_ar",
                    "price_monthly",
                    "price_yearly",
                    "trial_days",
                    "tier",
                    "users",
                    "clients",
                    "visible",
                )
            },
        ),
        (
            "Entitlements (JSON)",
            {
                "fields": ("features", "limits", "usage_limits_monthly"),
                "classes": ("collapse",),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        from subscriptions.plan_constraints import validate_plan_instance_uniqueness

        validate_plan_instance_uniqueness(obj, exclude_self=bool(obj.pk))
        super().save_model(request, obj, form, change)


class PaymentInline(admin.TabularInline):
    """Inline admin for Payment model"""

    model = Payment
    extra = 0
    fields = [
        "amount",
        "currency",
        "exchange_rate",
        "amount_usd",
        "target_plan",
        "billing_cycle",
        "payment_method",
        "payment_status",
        "tran_ref",
        "created_at",
    ]
    readonly_fields = ["created_at"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin configuration for Subscription model"""

    list_display = [
        "id",
        "company",
        "plan",
        "subscription_status",
        "billing_cycle",
        "start_date",
        "end_date",
        "current_period_start",
        "pending_plan",
        "is_active",
        "auto_renew",
        "created_at",
    ]
    list_filter = [
        "is_active",
        "auto_renew",
        "subscription_status",
        "billing_cycle",
        "plan",
        "pending_plan",
        "start_date",
        "end_date",
        "created_at",
    ]
    search_fields = [
        "company__name",
        "plan__name",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["start_date", "created_at", "updated_at"]
    inlines = [PaymentInline]

    fieldsets = (
        (
            "Subscription Information",
            {
                "fields": (
                    "company",
                    "plan",
                    "subscription_status",
                    "billing_cycle",
                    "start_date",
                    "end_date",
                    "current_period_start",
                    "is_active",
                    "auto_renew",
                )
            },
        ),
        (
            "Scheduled change (downgrade / paid → free)",
            {
                "fields": ("pending_plan", "pending_billing_cycle"),
                "classes": ("collapse",),
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Admin configuration for Payment model"""

    list_display = [
        "id",
        "subscription",
        "amount",
        "currency",
        "amount_usd",
        "target_plan",
        "billing_cycle",
        "payment_method",
        "payment_status",
        "created_at",
        "updated_at",
        "tran_ref",
    ]
    list_filter = [
        "payment_status",
        "payment_method",
        "currency",
        "billing_cycle",
        "target_plan",
        "created_at",
    ]
    search_fields = [
        "subscription__company__name",
        "payment_method",
        "payment_status",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Payment Information",
            {
                "fields": (
                    "subscription",
                    "amount",
                    "currency",
                    "exchange_rate",
                    "amount_usd",
                    "payment_method",
                    "payment_status",
                    "tran_ref",
                )
            },
        ),
        (
            "Checkout intent",
            {
                "fields": ("target_plan", "billing_cycle"),
                "description": "Plan and cycle selected at checkout (may differ until payment completes).",
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(InvoiceSequence)
class InvoiceSequenceAdmin(admin.ModelAdmin):
    list_display = ["year", "last_number", "updated_at"]
    ordering = ["-year"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Admin configuration for Invoice model"""

    list_display = [
        "invoice_number",
        "payment",
        "subscription",
        "amount",
        "currency",
        "due_date",
        "created_at",
    ]
    list_filter = [
        "currency",
        "due_date",
        "created_at",
    ]
    search_fields = [
        "invoice_number",
        "company_name",
        "plan_name",
        "subscription__company__name",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at", "payment", "subscription"]


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    """Admin configuration for Broadcast model"""

    list_display = [
        "subject",
        "targets",
        "broadcast_type",
        "status",
        "scheduled_at",
        "sent_at",
        "created_at",
    ]
    list_filter = [
        "status",
        "broadcast_type",
        "created_at",
    ]
    search_fields = [
        "subject",
        "content",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["sent_at", "created_at", "updated_at"]


@admin.register(PaymentGateway)
class PaymentGatewayAdmin(admin.ModelAdmin):
    """Admin configuration for PaymentGateway model"""

    list_display = [
        "name",
        "status",
        "enabled",
        "created_at",
    ]
    list_filter = [
        "status",
        "enabled",
        "created_at",
    ]
    search_fields = [
        "name",
        "description",
    ]
    ordering = ["name"]
    readonly_fields = ["created_at", "updated_at"]
