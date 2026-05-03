from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0032_client_profession"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="budget_max",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Optional upper bound when budget is a range; null means single value (budget only).",
                max_digits=12,
                null=True,
            ),
        ),
    ]
