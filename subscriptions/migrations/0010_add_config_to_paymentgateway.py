# Generated manually for Paytabs integration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0009_remove_paymentgateway_config_alter_broadcast_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentgateway',
            name='config',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

