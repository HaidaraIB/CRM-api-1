from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("settings", "0017_platform_whatsapp_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadstatus",
            name="auto_delete_after_hours",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="If set, leads in this status longer than this many hours are deleted by a scheduled job.",
                null=True,
            ),
        ),
    ]
