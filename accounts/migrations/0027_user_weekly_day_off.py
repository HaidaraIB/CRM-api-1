from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_user_fcm_tokens"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="weekly_day_off",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Weekly day off: 0=Monday .. 6=Sunday. Null means no recurring weekly off.",
                null=True,
            ),
        ),
    ]
