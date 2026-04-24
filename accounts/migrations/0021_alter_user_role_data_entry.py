# Generated manually for data_entry role

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0020_set_phone_verified_existing_users"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("admin", "ADMIN"),
                    ("supervisor", "SUPERVISOR"),
                    ("employee", "EMPLOYEE"),
                    ("data_entry", "DATA_ENTRY"),
                ],
                max_length=64,
            ),
        ),
    ]
