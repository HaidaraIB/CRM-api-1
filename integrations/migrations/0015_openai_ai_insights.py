# Generated manually for OpenAI AI integration

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0001_initial"),
        ("crm", "0037_client_residence_patient_file_number"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("integrations", "0014_sms_provider_otpiq"),
    ]

    operations = [
        migrations.CreateModel(
            name="OpenAISettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("api_key", models.TextField(blank=True, help_text="OpenAI API key (stored encrypted)", null=True)),
                ("is_enabled", models.BooleanField(default=False, help_text="Whether AI lead analysis is enabled")),
                ("model", models.CharField(default="gpt-4o-mini", help_text="OpenAI model id (e.g. gpt-4o-mini)", max_length=64)),
                ("auto_analyze_enabled", models.BooleanField(default=True, help_text="Run analysis on scheduled cron when enabled")),
                ("max_leads_per_run", models.PositiveIntegerField(default=20, help_text="Maximum leads analyzed per run (cost guard)")),
                ("last_analysis_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "company",
                    models.OneToOneField(
                        help_text="Company that owns these OpenAI settings",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="openai_settings",
                        to="companies.company",
                    ),
                ),
            ],
            options={
                "verbose_name": "OpenAI Settings",
                "verbose_name_plural": "OpenAI Settings",
                "db_table": "openai_settings",
            },
        ),
        migrations.CreateModel(
            name="ClientAIInsight",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ai_score", models.PositiveSmallIntegerField(default=0, help_text="AI priority score 0-100")),
                (
                    "priority_level",
                    models.CharField(
                        choices=[("high", "High"), ("medium", "Medium"), ("low", "Low")],
                        default="medium",
                        max_length=10,
                    ),
                ),
                ("summary", models.TextField(blank=True, default="")),
                ("reasoning", models.TextField(blank=True, null=True)),
                ("suggested_reminder_date", models.DateTimeField(blank=True, null=True)),
                ("suggested_task_notes", models.TextField(blank=True, null=True)),
                ("source_snapshot_hash", models.CharField(blank=True, default="", max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("dismissed", "Dismissed"),
                            ("expired", "Expired"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("analyzed_at", models.DateTimeField(auto_now_add=True)),
                ("model_used", models.CharField(blank=True, default="", max_length=64)),
                ("tokens_used", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_ai_insights",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_insights",
                        to="crm.client",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="client_ai_insights",
                        to="companies.company",
                    ),
                ),
                (
                    "created_client_task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_ai_insight",
                        to="crm.clienttask",
                    ),
                ),
            ],
            options={
                "db_table": "client_ai_insight",
                "ordering": ["-ai_score", "-analyzed_at"],
            },
        ),
        migrations.AddIndex(
            model_name="clientaiinsight",
            index=models.Index(fields=["company", "status", "-ai_score"], name="client_ai_i_company_8f3c2a_idx"),
        ),
        migrations.AddIndex(
            model_name="clientaiinsight",
            index=models.Index(fields=["client", "status"], name="client_ai_i_client__a91b4e_idx"),
        ),
        migrations.AddConstraint(
            model_name="clientaiinsight",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("client",),
                name="unique_pending_ai_insight_per_client",
            ),
        ),
    ]
