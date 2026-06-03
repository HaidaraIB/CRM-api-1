from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0017_alter_company_field_visit_enabled"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="last_auto_assigned_employee",
            field=models.ForeignKey(
                blank=True,
                help_text="Last employee chosen by smart auto-assign (tie-break rotation).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="auto_assign_round_robin_companies",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
