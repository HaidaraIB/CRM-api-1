# Generated manually - remove supervisors permission

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_remove_supervisorpermission_can_manage_clients'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='supervisorpermission',
            name='can_manage_supervisors',
        ),
    ]
