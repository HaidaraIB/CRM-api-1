from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0028_alter_user_role_reception_doctor"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="login_two_factor_enabled",
            field=models.BooleanField(
                default=True,
                help_text="When enabled, company owner must complete email 2FA at login (unless trusted device).",
            ),
        ),
    ]
