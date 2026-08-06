from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0032_user_login_lockout_and_systemsettings_lockout_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="work_start_time",
            field=models.TimeField(
                blank=True,
                help_text="Daily work start time (company timezone). Must be set together with work_end_time.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="work_end_time",
            field=models.TimeField(
                blank=True,
                help_text="Daily work end time (company timezone). Must be set together with work_start_time.",
                null=True,
            ),
        ),
    ]
