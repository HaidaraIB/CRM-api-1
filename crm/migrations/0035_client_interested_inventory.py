import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0034_client_notes"),
        ("real_estate", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="interested_developer",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional developer the lead is interested in (real estate).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="interested_leads",
                to="real_estate.developer",
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="interested_project",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional project the lead is interested in (real estate).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="interested_leads",
                to="real_estate.project",
            ),
        ),
        migrations.AddField(
            model_name="client",
            name="interested_unit",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional unit the lead is interested in (real estate).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="interested_leads",
                to="real_estate.unit",
            ),
        ),
    ]
