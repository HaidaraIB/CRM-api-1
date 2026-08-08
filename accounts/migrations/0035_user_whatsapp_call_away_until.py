from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0034_platform_content_and_can_manage_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="whatsapp_call_away_until",
            field=models.DateTimeField(
                blank=True,
                help_text="When set and in the future, user is Away for WhatsApp Cloud Calling (no incoming rings).",
                null=True,
            ),
        ),
    ]
