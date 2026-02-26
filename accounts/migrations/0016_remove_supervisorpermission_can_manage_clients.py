# Generated manually - remove clients permission (leads and clients are same entity)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_supervisor_role_and_supervisorpermission'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='supervisorpermission',
            name='can_manage_clients',
        ),
    ]
