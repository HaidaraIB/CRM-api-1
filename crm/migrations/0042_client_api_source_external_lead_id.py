# Generated manually for Custom Lead API

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0041_client_meta_qualification"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="external_lead_id",
            field=models.CharField(
                blank=True,
                help_text="Partner-provided idempotency key for API / custom form leads",
                max_length=255,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="client",
            name="source",
            field=models.CharField(
                choices=[
                    ("meta_lead_form", "Meta Lead Form"),
                    ("whatsapp", "WhatsApp"),
                    ("tiktok", "TikTok"),
                    ("api", "API / Custom Form"),
                    ("manual", "Manual"),
                    ("other", "Other"),
                ],
                default="manual",
                help_text="مصدر الليد",
                max_length=50,
            ),
        ),
        migrations.AddConstraint(
            model_name="client",
            constraint=models.UniqueConstraint(
                condition=models.Q(("external_lead_id__isnull", False)),
                fields=("company", "external_lead_id"),
                name="uniq_client_company_external_lead_id",
            ),
        ),
    ]
