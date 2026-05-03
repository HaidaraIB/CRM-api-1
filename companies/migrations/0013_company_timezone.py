from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0012_company_last_data_entry_assigned_employee"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="timezone",
            field=models.CharField(
                default="UTC",
                help_text="IANA timezone for business calendar (weekly day off, etc.).",
                max_length=64,
            ),
        ),
    ]
