from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('telephony', '0010_pbxcall_missed_notified_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='pbxcall',
            name='emergency_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
