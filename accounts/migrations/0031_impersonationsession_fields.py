# Generated for professional impersonation sessions

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0030_user_can_delete_clients"),
        ("companies", "0020_company_auto_assign_algorithm"),
    ]

    operations = [
        migrations.AddField(
            model_name="impersonationsession",
            name="used_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="impersonationsession",
            name="impersonator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="impersonation_sessions_started",
                to="accounts.user",
            ),
        ),
        migrations.AddField(
            model_name="impersonationsession",
            name="target_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="impersonation_sessions_as_target",
                to="accounts.user",
            ),
        ),
        migrations.AddField(
            model_name="impersonationsession",
            name="company",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="impersonation_sessions",
                to="companies.company",
            ),
        ),
        migrations.AlterField(
            model_name="impersonationsession",
            name="payload",
            field=models.JSONField(
                help_text="Dict: access, refresh, user, impersonation meta"
            ),
        ),
    ]
