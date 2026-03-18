# Generated manually for lead_company_name field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0023_clientcall_call_datetime'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='lead_company_name',
            field=models.CharField(blank=True, help_text='اسم شركة العميل / الليد (اختياري)', max_length=255, null=True),
        ),
    ]
