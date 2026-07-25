# Generated manually for Mujeb lead source

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0047_clientcall_follow_up_completed_clienttask_reminder_completed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="client",
            name="source",
            field=models.CharField(
                choices=[
                    ("meta_lead_form", "Meta Lead Form"),
                    ("whatsapp", "WhatsApp"),
                    ("tiktok", "TikTok"),
                    ("api", "API / Custom Form"),
                    ("mujeb", "Mujeb"),
                    ("manual", "Manual"),
                    ("other", "Other"),
                ],
                default="manual",
                help_text="مصدر الليد",
                max_length=50,
            ),
        ),
    ]
