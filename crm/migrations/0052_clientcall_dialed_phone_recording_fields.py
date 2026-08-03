# Generated manually — columns already exist on some local DBs (NOT NULL dialed_phone_number).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0051_whatsapp_calling"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="clientcall",
                    name="dialed_phone_number",
                    field=models.CharField(
                        blank=True,
                        default="",
                        help_text="E.164 / digits dialed or remote party for this call",
                        max_length=32,
                    ),
                ),
                migrations.AddField(
                    model_name="clientcall",
                    name="recording_duration_sec",
                    field=models.PositiveIntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="clientcall",
                    name="recording_status",
                    field=models.CharField(blank=True, default="", max_length=16),
                ),
                migrations.AddField(
                    model_name="clientcall",
                    name="recording_storage_key",
                    field=models.CharField(blank=True, default="", max_length=512),
                ),
            ],
            database_operations=[
                # Intentionally empty: columns already existed on some envs.
                # Actual ADD for missing DBs is in 0053_clientcall_ensure_*.
            ],
        ),
    ]
