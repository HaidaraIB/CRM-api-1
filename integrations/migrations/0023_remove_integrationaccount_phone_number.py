from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0022_pbx_integration'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='integrationaccount',
            name='phone_number',
        ),
    ]
