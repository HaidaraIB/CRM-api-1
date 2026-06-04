from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0019_systemsettings_feature_policies"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="maintenance_mode",
            field=models.BooleanField(
                default=False,
                help_text="When enabled, all API traffic is blocked except public status and critical webhooks.",
            ),
        ),
        migrations.AddField(
            model_name="systemsettings",
            name="maintenance_message",
            field=models.CharField(
                blank=True,
                default="The system is under maintenance. Please try again later.",
                help_text="Message shown to users while maintenance mode is active.",
                max_length=500,
            ),
        ),
    ]
