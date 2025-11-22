from django.contrib import admin
from .models import Client, Deal, Task, Campaign, ClientTask


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    """Admin configuration for Client model"""

    list_display = [
        "name",
        "priority",
        "type",
        "communication_way",
        "budget",
        "phone_number",
        "company",
        "assigned_to",
        "created_at",
    ]
    list_filter = [
        "priority",
        "type",
        "communication_way",
        "company",
        "assigned_to",
        "created_at",
    ]
    search_fields = [
        "name",
        "phone_number",
        "company__name",
        "assigned_to__username",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Client Information",
            {
                "fields": (
                    "name",
                    "priority",
                    "type",
                    "communication_way",
                    "budget",
                    "phone_number",
                )
            },
        ),
        (
            "Relations",
            {
                "fields": (
                    "company",
                    "assigned_to",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


class TaskInline(admin.TabularInline):
    """Inline admin for Task model"""

    model = Task
    extra = 0
    fields = ["stage", "notes", "reminder_date", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    """Admin configuration for Deal model"""

    list_display = [
        "id",
        "client",
        "company",
        "employee",
        "employee__username",
        "stage",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "stage",
        "company",
        "employee",
        "employee__username",
        "created_at",
    ]
    search_fields = [
        "client__name",
        "company__name",
        "employee",
        "employee__username",
        "stage",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [TaskInline]

    fieldsets = (
        (
            "Deal Information",
            {
                "fields": (
                    "client",
                    "company",
                    "employee",
                    "stage",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    """Admin configuration for Task model"""

    list_display = [
        "id",
        "deal",
        "stage",
        "reminder_date",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "stage",
        "reminder_date",
        "created_at",
    ]
    search_fields = [
        "deal__client__name",
        "stage",
        "notes",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Task Information",
            {
                "fields": (
                    "deal",
                    "stage",
                    "notes",
                    "reminder_date",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(ClientTask)
class ClientTaskAdmin(admin.ModelAdmin):
    """Admin configuration for ClientTask model"""

    list_display = [
        "id",
        "client",
        "stage",
        "reminder_date",
        "created_by",
        "created_at",
        "updated_at",
    ]
    list_filter = [
        "stage",
        "reminder_date",
        "created_at",
        "created_by",
    ]
    search_fields = [
        "client__name",
        "stage",
        "notes",
        "created_by__username",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Client Task Information",
            {
                "fields": (
                    "client",
                    "stage",
                    "notes",
                    "reminder_date",
                    "created_by",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
