from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0042_whatsappcall_takeover_requested_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappaccount",
            name="call_hours_enabled",
            field=models.BooleanField(
                default=False,
                help_text="When true, enforce weekly call hours (synced to Meta call_hours when possible).",
            ),
        ),
        migrations.AddField(
            model_name="whatsappaccount",
            name="call_hours_timezone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="IANA timezone for call hours (e.g. Asia/Baghdad).",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="whatsappaccount",
            name="call_hours_weekly",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Per-day schedule: {monday: {closed, open, close}, ...} times HH:MM.",
            ),
        ),
        migrations.AddField(
            model_name="whatsappaccount",
            name="out_of_hours_message",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Text sent to the customer when an inbound call arrives outside call hours.",
            ),
        ),
    ]
