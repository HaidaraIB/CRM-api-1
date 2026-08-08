# Generated manually for WhatsApp agent takeover request

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0041_whatsapp_call_livekit_supervision"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappcall",
            name="takeover_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
