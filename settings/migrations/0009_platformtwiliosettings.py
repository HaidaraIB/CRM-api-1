# Generated for Platform Twilio (SMS broadcast from admin panel)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0008_add_is_default_to_channel_stage_callmethod'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlatformTwilioSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account_sid', models.CharField(blank=True, help_text='Twilio Account SID', max_length=64, null=True)),
                ('twilio_number', models.CharField(blank=True, help_text='Twilio sender number', max_length=20, null=True)),
                ('auth_token', models.TextField(blank=True, help_text='Auth Token (stored encrypted)', null=True)),
                ('sender_id', models.CharField(blank=True, help_text='Sender ID (optional, alphanumeric)', max_length=11, null=True)),
                ('is_enabled', models.BooleanField(default=False, help_text='Enable platform SMS broadcast')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Platform Twilio Settings',
                'verbose_name_plural': 'Platform Twilio Settings',
                'db_table': 'settings_platform_twilio_settings',
                'ordering': ['-updated_at'],
            },
        ),
    ]
