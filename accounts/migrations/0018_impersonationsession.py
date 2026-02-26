# Generated for impersonation handoff (DB-backed so all workers share codes)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_remove_supervisorpermission_can_manage_supervisors'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImpersonationSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(db_index=True, max_length=64, unique=True)),
                ('payload', models.JSONField(help_text='Dict: access, refresh, user')),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'accounts_impersonation_session',
                'ordering': ['-created_at'],
            },
        ),
    ]
