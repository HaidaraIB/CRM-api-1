# Generated manually for lead creator tracking

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("crm", "0029_clientvisit_visit_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text="CRM user who created this lead (manual/API); null for integrations",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_clients",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
