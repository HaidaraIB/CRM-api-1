# Generated manually for Task.completed_at

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0049_clientphonenumber_company_phone_normalized'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='completed_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When this deal task was marked done',
                null=True,
            ),
        ),
    ]
