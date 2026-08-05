# Soft-delete legacy WhatsApp inbound bell-inbox rows (FCM-only going forward).

from django.db import migrations
from django.utils import timezone


def soft_delete_whatsapp_inbound_inbox(apps, schema_editor):
    Notification = apps.get_model('notifications', 'Notification')
    Notification.objects.filter(
        type='whatsapp_message_received',
        deleted_at__isnull=True,
    ).update(deleted_at=timezone.now())


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0015_alter_notification_type_and_more'),
    ]

    operations = [
        migrations.RunPython(soft_delete_whatsapp_inbound_inbox, noop),
    ]
