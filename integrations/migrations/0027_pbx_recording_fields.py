from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "integrations",
            "0026_rename_integrations_company_linkedid_idx_integration_company_a9c108_idx",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="pbxsettings",
            name="connector_base_url",
            field=models.CharField(
                blank=True,
                default="",
                help_text="LAN connector base URL, e.g. http://192.168.1.50:8787 (for recording fetch).",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="pbxcallrecord",
            name="recording_storage_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="pbxcallrecord",
            name="recording_uploaded",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="pbxcallrecord",
            name="recording_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped"),
                ],
                db_index=True,
                default="skipped",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="pbxcallrecord",
            name="recording_path",
            field=models.TextField(blank=True, default=""),
        ),
    ]
