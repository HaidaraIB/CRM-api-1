from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0018_leadstatus_auto_delete_after_hours"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="feature_policies",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Feature gating policies. Schema: {feature: {global_enabled, global_message, company_overrides{company_id:{enabled,message}}}}",
            ),
        ),
    ]
