from django.contrib import admin
from .models import Plan, Subscription, Payment, Invoice, Broadcast, PaymentGateway


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Admin configuration for Plan model"""

    list_display = [
        "name",
        "name_ar",
        "price_monthly",
        "price_yearly",
        "trial_days",
        "visible",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "visible",
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
                    "users",
                    "clients",
                    "storage",
                    "visible",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


class PaymentInline(admin.TabularInline):
    """Inline admin for Payment model"""

    model = Payment
    extra = 0
    fields = ["amount", "payment_method", "payment_status", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin configuration for Subscription model"""

    list_display = [
        "id",
        "company",
        "plan",
        "start_date",
        "end_date",
        "is_active",
        "auto_renew",
        "created_at",
    ]
    list_filter = [
        "is_active",
        "auto_renew",
        "plan",
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
                    "start_date",
                    "end_date",
                    "is_active",
                    "auto_renew",
                )
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
        "payment_method",
        "payment_status",
        "created_at",
        "updated_at",
        "tran_ref",
    ]
    list_filter = [
        "payment_status",
        "payment_method",
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
                    "payment_method",
                    "payment_status",
                    "tran_ref",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Admin configuration for Invoice model"""

    list_display = [
        "invoice_number",
        "subscription",
        "amount",
        "due_date",
        "status",
        "created_at",
    ]
    list_filter = [
        "status",
        "due_date",
        "created_at",
    ]
    search_fields = [
        "invoice_number",
        "subscription__company__name",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    """Admin configuration for Broadcast model"""

    list_display = [
        "subject",
        "target",
        "status",
        "scheduled_at",
        "sent_at",
        "created_at",
    ]
    list_filter = [
        "status",
        "target",
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
