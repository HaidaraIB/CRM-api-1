# Generated manually for Mujeb integration platform

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0031_campaign_message_logs"),
    ]

    operations = [
        migrations.AlterField(
            model_name="integrationaccount",
            name="platform",
            field=models.CharField(
                choices=[
                    ("meta", "Meta (Facebook/Instagram)"),
                    ("tiktok", "TikTok"),
                    ("whatsapp", "WhatsApp Business"),
                    ("api", "Lead API / Custom Form"),
                    ("mujeb", "Mujeb"),
                ],
                help_text="نوع المنصة (Meta, TikTok, WhatsApp)",
                max_length=50,
            ),
        ),
    ]
