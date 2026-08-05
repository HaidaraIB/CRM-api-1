# Generated manually for WhatsApp location messages

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0039_twiliosettings_lead_created_whatsapp"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadwhatsappmessage",
            name="location_latitude",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text="WhatsApp location message latitude.",
                max_digits=9,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="leadwhatsappmessage",
            name="location_longitude",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text="WhatsApp location message longitude.",
                max_digits=9,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="leadwhatsappmessage",
            name="location_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="leadwhatsappmessage",
            name="location_address",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AlterField(
            model_name="leadwhatsappmessage",
            name="attachment_kind",
            field=models.CharField(
                blank=True,
                choices=[
                    ("image", "Image"),
                    ("video", "Video"),
                    ("audio", "Audio"),
                    ("document", "Document"),
                    ("location", "Location"),
                ],
                max_length=16,
                null=True,
            ),
        ),
    ]
