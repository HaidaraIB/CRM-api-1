# Generated manually for Platform WhatsApp Cloud API settings

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0016_seed_company_settings_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformWhatsAppSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone_number_id", models.CharField(blank=True, help_text="WhatsApp Cloud API phone_number_id", max_length=64, null=True)),
                ("access_token", models.TextField(blank=True, help_text="Permanent access token (stored encrypted)", null=True)),
                (
                    "graph_api_version",
                    models.CharField(blank=True, default="v25.0", help_text="Graph API version, e.g. v25.0", max_length=16, null=True),
                ),
                (
                    "otp_template_name",
                    models.CharField(blank=True, help_text="Approved authentication template name for OTP", max_length=128, null=True),
                ),
                (
                    "otp_template_lang",
                    models.CharField(blank=True, default="en", help_text="Template language code", max_length=16, null=True),
                ),
                (
                    "admin_template_name",
                    models.CharField(blank=True, help_text="Optional utility template for admin outbound messages", max_length=128, null=True),
                ),
                (
                    "admin_template_lang",
                    models.CharField(blank=True, default="en", max_length=16, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Platform WhatsApp Settings",
                "verbose_name_plural": "Platform WhatsApp Settings",
                "db_table": "settings_platform_whatsapp_settings",
                "ordering": ["-updated_at"],
            },
        ),
    ]
