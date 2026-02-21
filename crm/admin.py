from django.contrib import admin
from .models import Client, Deal, Task, Campaign, ClientTask, ClientPhoneNumber, ClientEvent, ClientCall


class ClientPhoneNumberInline(admin.TabularInline):
    """Inline admin for ClientPhoneNumber model"""

    model = ClientPhoneNumber
    extra = 1
    fields = ["phone_number", "phone_type", "is_primary", "notes"]
    ordering = ["-is_primary", "phone_type"]


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
        "phone_numbers__phone_number",
        "company__name",
        "assigned_to__username",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [ClientPhoneNumberInline]

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


@admin.register(ClientPhoneNumber)
class ClientPhoneNumberAdmin(admin.ModelAdmin):
    """Admin configuration for ClientPhoneNumber model"""

    list_display = [
        "client",
        "phone_number",
        "phone_type",
        "is_primary",
        "created_at",
    ]
    list_filter = [
        "phone_type",
        "is_primary",
        "created_at",
    ]
    search_fields = [
        "client__name",
        "phone_number",
    ]
    ordering = ["-is_primary", "phone_type", "-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (
            "Phone Number Information",
            {
                "fields": (
                    "client",
                    "phone_number",
                    "phone_type",
                    "is_primary",
                    "notes",
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
        "get_employee_username",
        "stage",
        "created_at",
        "updated_at",
        "value",
        "start_date",
        "closed_date",
        "discount_percentage",
        "discount_amount",
        "sales_commission_percentage",
        "sales_commission_amount",
        "description",
        "unit",
        "project",
    ]
    list_filter = [
        "stage",
        "company",
        "employee",
        "created_at",
        "value",
        "start_date",
        "closed_date",
        "discount_percentage",
        "discount_amount",
        "sales_commission_percentage",
        "sales_commission_amount",
        "description",
        "unit",
        "project",
    ]
    search_fields = [
        "client__name",
        "company__name",
        "employee__username",
        "stage",
        "value",
        "start_date",
        "closed_date",
        "discount_percentage",
        "discount_amount",
        "sales_commission_percentage",
        "sales_commission_amount",
        "description",
        "unit",
        "project",
    ]

    def get_employee_username(self, obj):
        """Return the username of the employee"""
        return obj.employee.username if obj.employee else "-"

    get_employee_username.short_description = "Employee Username"
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
                    "payment_method",
                    "status",
                    "value",
                    "start_date",
                    "closed_date",
                    "discount_percentage",
                    "discount_amount",
                    "sales_commission_percentage",
                    "sales_commission_amount",
                    "description",
                    "unit",
                    "project",
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


@admin.register(ClientEvent)
class ClientEventAdmin(admin.ModelAdmin):
    """Admin configuration for ClientEvent model"""

    list_display = [
        "id",
        "client",
        "event_type",
        "old_value",
        "new_value",
        "created_by",
        "created_at",
    ]
    list_filter = ["event_type", "created_at", "created_by"]
    search_fields = ["client__name", "event_type", "notes"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at"]
    raw_id_fields = ["client", "created_by"]


@admin.register(ClientCall)
class ClientCallAdmin(admin.ModelAdmin):
    """Admin configuration for ClientCall model"""

    list_display = [
        "id",
        "client",
        "call_method",
        "call_datetime",
        "follow_up_date",
        "created_by",
        "created_at",
    ]
    list_filter = ["call_method", "created_at", "created_by"]
    search_fields = ["client__name", "notes"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["client", "call_method", "created_by"]


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    """Admin configuration for Campaign model"""

    list_display = [
        "id",
        "code",
        "name",
        "budget",
        "is_active",
        "company",
        "created_at",
        "updated_at",
    ]
    list_filter = ["is_active", "company", "created_at"]
    search_fields = ["code", "name", "company__name"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]
