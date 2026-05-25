# Generated manually for Custom Lead API

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("integrations", "0018_aimanagementreport"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyLeadApiKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="Label for this key (e.g. Website form, Mobile app)", max_length=128)),
                ("key_prefix", models.CharField(help_text="First characters of the key for display in the UI", max_length=16)),
                ("key_hash", models.CharField(db_index=True, help_text="SHA-256 hash of the full API key", max_length=64)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lead_api_keys",
                        to="companies.company",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_lead_api_keys",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "company_lead_api_keys",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="companyleadapikey",
            index=models.Index(fields=["company", "is_active"], name="company_lea_company_6a8f2d_idx"),
        ),
    ]
