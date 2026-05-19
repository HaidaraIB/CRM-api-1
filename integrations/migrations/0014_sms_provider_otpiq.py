from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0013_alter_twiliosettings_lead_created_sms_template'),
    ]

    operations = [
        migrations.AddField(
            model_name='twiliosettings',
            name='provider',
            field=models.CharField(
                choices=[('twilio', 'Twilio'), ('otpiq', 'OTPIQ')],
                default='twilio',
                help_text='Active SMS provider for this company',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='twiliosettings',
            name='otpiq_api_key',
            field=models.TextField(blank=True, help_text='OTPIQ API key (stored encrypted)', null=True),
        ),
        migrations.AddField(
            model_name='twiliosettings',
            name='otpiq_route_provider',
            field=models.CharField(
                default='sms',
                help_text='OTPIQ provider route (e.g. sms, whatsapp-sms)',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='leadsmsmessage',
            name='provider',
            field=models.CharField(
                blank=True,
                choices=[('twilio', 'Twilio'), ('otpiq', 'OTPIQ')],
                help_text='SMS provider used to send this message',
                max_length=16,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='leadsmsmessage',
            name='external_message_id',
            field=models.CharField(
                blank=True,
                help_text='Provider message id (Twilio SID or OTPIQ smsId)',
                max_length=64,
                null=True,
            ),
        ),
    ]
