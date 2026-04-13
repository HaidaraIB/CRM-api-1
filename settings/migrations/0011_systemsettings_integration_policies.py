from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0010_systemsettings_mobile_version_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="integration_policies",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Integration gating policies by platform. Schema: {platform: {global_enabled, global_message, company_overrides{company_id:{enabled,message}}}}",
            ),
        ),
    ]
