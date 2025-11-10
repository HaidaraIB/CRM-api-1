from django.contrib import admin
from .models import Plan, Subscription, Payment


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Admin configuration for Plan model"""

    list_display = [
        "name",
        "price_monthly",
        "price_yearly",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "created_at",
        "updated_at",
    ]
    search_fields = [
        "name",
        "description",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Plan Information",
            {
                "fields": (
                    "name",
                    "description",
                    "price_monthly",
                    "price_yearly",
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
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
