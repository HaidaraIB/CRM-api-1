# Generated manually for Twilio SMS integration

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0001_initial'),
        ('crm', '0019_client_campaign_client_integration_account_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('integrations', '0003_whatsapp_account'),
    ]

    operations = [
        migrations.CreateModel(
            name='TwilioSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account_sid', models.CharField(blank=True, help_text='Twilio Account SID', max_length=64, null=True)),
                ('twilio_number', models.CharField(blank=True, help_text='رقم الإرسال (Twilio Number)', max_length=20, null=True)),
                ('auth_token', models.TextField(blank=True, help_text='Auth Token (مخزن مشفراً)', null=True)),
                ('sender_id', models.CharField(blank=True, help_text='اسم المرسل (Sender ID) - اختياري', max_length=11, null=True)),
                ('is_enabled', models.BooleanField(default=False, help_text='الربط مفعل')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.OneToOneField(help_text='الشركة المالكة للإعدادات', on_delete=django.db.models.deletion.CASCADE, related_name='twilio_settings', to='companies.company')),
            ],
            options={
                'verbose_name': 'Twilio SMS Settings',
                'verbose_name_plural': 'Twilio SMS Settings',
                'db_table': 'twilio_settings',
            },
        ),
        migrations.CreateModel(
            name='LeadSMSMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone_number', models.CharField(help_text='رقم الهاتف الذي أُرسلت إليه الرسالة', max_length=20)),
                ('body', models.TextField(help_text='نص الرسالة')),
                ('direction', models.CharField(choices=[('outbound', 'Outbound'), ('inbound', 'Inbound')], default='outbound', max_length=10)),
                ('twilio_sid', models.CharField(blank=True, help_text='معرف الرسالة من Twilio', max_length=64, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('client', models.ForeignKey(help_text='العميل المحتمل (الليد)', on_delete=django.db.models.deletion.CASCADE, related_name='sms_messages', to='crm.client')),
                ('created_by', models.ForeignKey(blank=True, help_text='المستخدم الذي أرسل الرسالة', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sent_sms_messages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Lead SMS Message',
                'verbose_name_plural': 'Lead SMS Messages',
                'db_table': 'lead_sms_messages',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='leadsmsmessage',
            index=models.Index(fields=['client', 'created_at'], name='lead_sms_msg_client_created_idx'),
        ),
    ]
