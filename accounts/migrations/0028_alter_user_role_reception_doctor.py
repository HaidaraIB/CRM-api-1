# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0027_user_weekly_day_off"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("super_admin", "SUPER_ADMIN"),
                    ("admin", "ADMIN"),
                    ("supervisor", "SUPERVISOR"),
                    ("employee", "EMPLOYEE"),
                    ("data_entry", "DATA_ENTRY"),
                    ("reception", "RECEPTION"),
                    ("doctor", "DOCTOR"),
                ],
                max_length=64,
            ),
        ),
    ]
