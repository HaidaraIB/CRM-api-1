# Generated manually for WhatsApp call LiveKit supervision fields

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("integrations", "0040_leadwhatsappmessage_location_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappcall",
            name="supervisor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="supervised_whatsapp_calls",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="whatsappcall",
            name="supervision_mode",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("listen", "Listen"),
                    ("whisper", "Whisper"),
                    ("barge", "Barge"),
                    ("takeover", "Takeover"),
                ],
                db_index=True,
                default="none",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="whatsappcall",
            name="livekit_room_name",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="whatsappcall",
            name="livekit_egress_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
