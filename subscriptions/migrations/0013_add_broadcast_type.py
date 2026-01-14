# Generated migration for adding broadcast_type field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0012_alter_subscription_auto_renew'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='broadcast_type',
            field=models.CharField(
                choices=[('email', 'Email'), ('push', 'Push')],
                default='email',
                max_length=10
            ),
        ),
    ]
