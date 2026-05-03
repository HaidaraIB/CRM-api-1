# Generated manually for profession field on Client (lead)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0031_alter_client_created_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="profession",
            field=models.CharField(
                blank=True,
                help_text="المهنة (اختياري)",
                max_length=255,
                null=True,
            ),
        ),
    ]
