# Generated manually for call_center role

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0036_supervisorpermission_can_manage_whatsapp_calls_and_more"),
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
                    ("call_center", "CALL_CENTER"),
                ],
                max_length=64,
            ),
        ),
    ]
