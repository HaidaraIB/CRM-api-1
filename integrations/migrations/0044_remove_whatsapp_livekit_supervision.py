# Remove WhatsApp call LiveKit / SFU supervision fields

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0043_whatsappaccount_call_hours"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="whatsappcall",
            name="supervisor",
        ),
        migrations.RemoveField(
            model_name="whatsappcall",
            name="supervision_mode",
        ),
        migrations.RemoveField(
            model_name="whatsappcall",
            name="livekit_room_name",
        ),
        migrations.RemoveField(
            model_name="whatsappcall",
            name="livekit_egress_id",
        ),
        migrations.RemoveField(
            model_name="whatsappcall",
            name="takeover_requested_at",
        ),
    ]
