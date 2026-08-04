# Generated manually for WhatsApp CS-window sender pinning

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0036_remove_softphone'),
    ]

    operations = [
        migrations.AddField(
            model_name='leadwhatsappmessage',
            name='phone_number_id',
            field=models.CharField(
                blank=True,
                help_text='Meta business phone_number_id that received/sent this message (for CS-window routing).',
                max_length=64,
                null=True,
            ),
        ),
    ]
