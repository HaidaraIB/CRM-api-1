# Generated for OAuth state storage (shared across workers)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0006_messagetemplate'),
    ]

    operations = [
        migrations.CreateModel(
            name='OAuthState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state', models.CharField(db_index=True, max_length=64, unique=True)),
                ('account_id', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'integration_oauth_states',
                'ordering': ['-created_at'],
            },
        ),
    ]
