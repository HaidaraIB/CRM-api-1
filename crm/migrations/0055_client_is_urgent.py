from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0054_clienttask_reminder_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="is_urgent",
            field=models.BooleanField(
                default=False,
                help_text="When True on create, prefer an assignee currently within working hours.",
            ),
        ),
    ]
