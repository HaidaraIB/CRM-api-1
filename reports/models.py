"""
Append-only lead history used by the reports hub.

``ClientEvent`` stores display names and is skipped by several code paths, so it cannot
answer "which statuses did this lead go through" reliably. These tables are written through
``reports.recorder`` (the single write path) and hold IDs plus name snapshots.
"""
from django.conf import settings
from django.db import models


class LeadStatusTransition(models.Model):
    SOURCE_MANUAL = "manual"
    SOURCE_BULK = "bulk"
    SOURCE_AUTOMATION = "automation"
    SOURCE_INTEGRATION = "integration"
    SOURCE_SYSTEM = "system"
    SOURCE_BACKFILL = "backfill"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_BULK, "Bulk"),
        (SOURCE_AUTOMATION, "Automation"),
        (SOURCE_INTEGRATION, "Integration"),
        (SOURCE_SYSTEM, "System"),
        (SOURCE_BACKFILL, "Backfill"),
    ]

    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="lead_status_transitions")
    client = models.ForeignKey("crm.Client", on_delete=models.CASCADE, related_name="status_transitions")
    from_status = models.ForeignKey(
        "settings.LeadStatus", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    to_status = models.ForeignKey(
        "settings.LeadStatus", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    from_status_name = models.CharField(max_length=255, blank=True, default="")
    to_status_name = models.CharField(max_length=255, blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField()
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    reason = models.TextField(blank=True, default="")
    seconds_in_previous = models.BigIntegerField(
        null=True, blank=True, help_text="Seconds the lead spent in from_status before this change."
    )
    is_backfilled = models.BooleanField(default=False)

    class Meta:
        db_table = "reports_lead_status_transition"
        ordering = ["changed_at", "id"]
        indexes = [
            models.Index(fields=["company", "changed_at"], name="rpt_trans_co_at"),
            models.Index(fields=["company", "to_status", "changed_at"], name="rpt_trans_co_to_at"),
            models.Index(fields=["client", "changed_at"], name="rpt_trans_client_at"),
        ]


class LeadAssignmentPeriod(models.Model):
    KIND_CALL = "call"
    KIND_TASK = "task"
    KIND_VISIT = "visit"
    KIND_WHATSAPP = "whatsapp"
    KIND_SMS = "sms"
    KIND_STATUS = "status_change"
    KIND_DEAL = "deal"
    KIND_NOTE = "note"
    KIND_CHOICES = [
        (KIND_CALL, "Call"),
        (KIND_TASK, "Task"),
        (KIND_VISIT, "Visit"),
        (KIND_WHATSAPP, "WhatsApp"),
        (KIND_SMS, "SMS"),
        (KIND_STATUS, "Status change"),
        (KIND_DEAL, "Deal"),
        (KIND_NOTE, "Note"),
    ]

    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="lead_assignment_periods")
    client = models.ForeignKey("crm.Client", on_delete=models.CASCADE, related_name="assignment_periods")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lead_assignment_periods")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    source = models.CharField(max_length=16, default="manual")
    assigned_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    first_response_kind = models.CharField(max_length=16, choices=KIND_CHOICES, blank=True, default="")
    response_seconds = models.BigIntegerField(null=True, blank=True, help_text="Raw elapsed seconds to first response.")
    business_response_seconds = models.BigIntegerField(
        null=True, blank=True, help_text="Seconds to first response counting only work hours."
    )
    is_backfilled = models.BooleanField(default=False)

    class Meta:
        db_table = "reports_lead_assignment_period"
        ordering = ["assigned_at", "id"]
        indexes = [
            models.Index(fields=["company", "user", "assigned_at"], name="rpt_asg_co_user_at"),
            models.Index(fields=["company", "assigned_at"], name="rpt_asg_co_at"),
            models.Index(fields=["client", "assigned_at"], name="rpt_asg_client_at"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["client"], condition=models.Q(ended_at__isnull=True), name="rpt_asg_one_open_per_client"
            )
        ]


class ConversationReply(models.Model):
    CHANNEL_WHATSAPP = "whatsapp"
    CHANNEL_SMS = "sms"
    CHANNEL_CHOICES = [(CHANNEL_WHATSAPP, "WhatsApp"), (CHANNEL_SMS, "SMS")]

    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="conversation_replies")
    client = models.ForeignKey("crm.Client", on_delete=models.CASCADE, related_name="conversation_replies")
    channel = models.CharField(max_length=16, choices=CHANNEL_CHOICES)
    inbound_at = models.DateTimeField()
    replied_at = models.DateTimeField()
    replied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    wall_seconds = models.BigIntegerField()
    business_seconds = models.BigIntegerField(null=True, blank=True)
    is_backfilled = models.BooleanField(default=False)

    class Meta:
        db_table = "reports_conversation_reply"
        ordering = ["replied_at", "id"]
        indexes = [
            models.Index(fields=["company", "replied_at"], name="rpt_reply_co_at"),
            models.Index(fields=["company", "replied_by", "replied_at"], name="rpt_reply_co_user_at"),
        ]
