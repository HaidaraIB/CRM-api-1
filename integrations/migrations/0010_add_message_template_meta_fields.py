# Generated manually for Meta WhatsApp template sync and status

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0009_rename_lead_whatsa_client__a1b2c3_idx_lead_whatsa_client__b0356e_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='messagetemplate',
            name='meta_template_id',
            field=models.CharField(blank=True, help_text='معرف القالب في Meta بعد الإرسال', max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='messagetemplate',
            name='meta_status',
            field=models.CharField(blank=True, help_text='حالة القالب في Meta: PENDING, APPROVED, REJECTED', max_length=32, null=True),
        ),
    ]
