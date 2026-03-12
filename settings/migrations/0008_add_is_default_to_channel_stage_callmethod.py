# Generated manually for is_default on Channel, LeadStage, CallMethod

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0007_add_backup_schedule_systemsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='is_default',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name='channel',
            options={'ordering': ['-is_default', '-created_at'], 'unique_together': {('name', 'company')}},
        ),
        migrations.AddField(
            model_name='leadstage',
            name='is_default',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name='leadstage',
            options={'ordering': ['-is_default', 'order', 'name'], 'db_table': 'settings_lead_stage', 'unique_together': {('name', 'company')}},
        ),
        migrations.AddField(
            model_name='callmethod',
            name='is_default',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name='callmethod',
            options={'ordering': ['-is_default', 'name'], 'db_table': 'settings_call_method', 'unique_together': {('name', 'company')}},
        ),
    ]
