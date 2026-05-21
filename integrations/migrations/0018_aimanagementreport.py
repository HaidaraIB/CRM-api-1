from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0001_initial"),
        ("integrations", "0017_clientaiinsight_bilingual"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIManagementReport",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("generated_at", models.DateTimeField(auto_now=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("model_used", models.CharField(blank=True, default="", max_length=64)),
                ("tokens_used", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "company",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_management_report",
                        to="companies.company",
                    ),
                ),
            ],
            options={
                "db_table": "ai_management_report",
            },
        ),
    ]
