# Generated manually for multi-target support

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0014_alter_broadcast_broadcast_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='targets',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
