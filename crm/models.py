from django.db import models
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


class CommunicationWay(Enum):
    CALL = "call"
    WHATSAPP = "whatsapp"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class ClientStatus(Enum):
    UNTOUCHED = "untouched"
    TOUCHED = "touched"
    FOLLOWING = "following"
    MEETING = "meeting"
    NO_ANSWER = "no_answer"
    OUT_OF_SERVICE = "out_of_service"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class Client(models.Model):
    name = models.CharField(max_length=255)
    priority = models.CharField(max_length=10, choices=Priority.choices())
    type = models.CharField(max_length=20, choices=Type.choices())
    communication_way = models.CharField(
        max_length=20, choices=CommunicationWay.choices()
    )
    status = models.CharField(
        max_length=20,
        choices=ClientStatus.choices(),
        default=ClientStatus.UNTOUCHED.value,
    )

    budget = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class TaskStage(Enum):
    FOLLOWING = "following"
    MEETING = "meeting"
    DONE_MEETING = "done_meeting"
    FOLLOW_AFTER_MEETING = "follow_after_meeting"
    RESCHEDULE_MEETING = "reschedule_meeting"
    CANCELLATION = "cancellation"
    NO_ANSWER = "no_answer"
    OUT_OF_SERVICE = "out_of_service"
    NOT_INTERESTED = "not_interested"
    WHATSAPP_PENDING = "whatsapp_pending"
    HOLD = "hold"
    BROKER = "broker"
    RESALE = "resale"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class CancelReason(Enum):
    LOW_BUDGET = "low_budget"
    NOT_INTERESTED = "not_interested"
    CHANGE_OPINION = "change_opinion"
    ALREADY_BOUGHT = "already_bought"
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
    stage = models.CharField(max_length=50, choices=DealStage.choices())
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.client.name} - {self.stage}"


class Task(models.Model):
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="tasks")
    stage = models.CharField(max_length=50, choices=TaskStage.choices())
    notes = models.TextField(blank=True, null=True)
    reminder_date = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.deal.client.name} - {self.stage}"


class ClientTask(models.Model):
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="client_tasks"
    )
    stage = models.CharField(max_length=50, choices=ClientStatus.choices())
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
        return f"{self.client.name} - {self.stage}"


class Campaign(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="campaigns"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
