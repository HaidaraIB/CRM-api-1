from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0033_client_budget_max"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="notes",
            field=models.TextField(
                blank=True,
                null=True,
                help_text="Optional free-form notes on this lead (not activity/task notes).",
            ),
        ),
    ]
