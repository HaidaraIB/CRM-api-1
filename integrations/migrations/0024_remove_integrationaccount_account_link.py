from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0023_remove_integrationaccount_phone_number'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='integrationaccount',
            name='account_link',
        ),
    ]
