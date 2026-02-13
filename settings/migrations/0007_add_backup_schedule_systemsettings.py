# Generated manually for backup_schedule on SystemSettings

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0006_callmethod'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='backup_schedule',
            field=models.CharField(
                choices=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
                default='daily',
                help_text='Backup schedule: daily, weekly, or monthly',
                max_length=20,
            ),
        ),
    ]
