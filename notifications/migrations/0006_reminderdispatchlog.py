from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("notifications", "0005_notification_deleted_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReminderDispatchLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("notification_type", models.CharField(choices=[("new_lead", "New Lead"), ("lead_no_follow_up", "Lead No Follow Up"), ("lead_reengaged", "Lead Reengaged"), ("lead_contact_failed", "Lead Contact Failed"), ("lead_status_changed", "Lead Status Changed"), ("lead_assigned", "Lead Assigned"), ("lead_transferred", "Lead Transferred"), ("lead_updated", "Lead Updated"), ("lead_reminder", "Lead Reminder"), ("whatsapp_message_received", "WhatsApp Message Received"), ("whatsapp_template_sent", "WhatsApp Template Sent"), ("whatsapp_send_failed", "WhatsApp Send Failed"), ("whatsapp_waiting_response", "WhatsApp Waiting Response"), ("campaign_performance", "Campaign Performance"), ("campaign_low_performance", "Campaign Low Performance"), ("campaign_stopped", "Campaign Stopped"), ("campaign_budget_alert", "Campaign Budget Alert"), ("task_created", "Task Created"), ("task_reminder", "Task Reminder"), ("task_completed", "Task Completed"), ("call_reminder", "Call Reminder"), ("deal_created", "Deal Created"), ("deal_updated", "Deal Updated"), ("deal_closed", "Deal Closed"), ("deal_reminder", "Deal Reminder"), ("daily_report", "Daily Report"), ("weekly_report", "Weekly Report"), ("top_employee", "Top Employee"), ("login_from_new_device", "Login from New Device"), ("broadcast", "Broadcast"), ("system_update", "System Update"), ("subscription_expiring", "Subscription Expiring"), ("payment_failed", "Payment Failed"), ("subscription_expired", "Subscription Expired"), ("general", "General")], max_length=50)),
                ("minutes_before", models.IntegerField(default=15)),
                ("scheduled_for", models.DateTimeField(help_text="The original reminder datetime (e.g. reminder_date / follow_up_date)")),
                ("object_id", models.CharField(max_length=64)),
                ("push_sent", models.BooleanField(default=False)),
                ("email_sent", models.BooleanField(default=False)),
                ("last_error", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("content_type", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="contenttypes.contenttype")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reminder_dispatch_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "notification_reminder_dispatch_log",
            },
        ),
        migrations.AddIndex(
            model_name="reminderdispatchlog",
            index=models.Index(fields=["user", "notification_type", "scheduled_for"], name="reminder_user_type_time_idx"),
        ),
        migrations.AddIndex(
            model_name="reminderdispatchlog",
            index=models.Index(fields=["content_type", "object_id"], name="reminder_object_idx"),
        ),
        migrations.AddConstraint(
            model_name="reminderdispatchlog",
            constraint=models.UniqueConstraint(fields=("user", "notification_type", "content_type", "object_id", "scheduled_for", "minutes_before"), name="unique_reminder_dispatch_per_user_object_time_offset_type"),
        ),
    ]

