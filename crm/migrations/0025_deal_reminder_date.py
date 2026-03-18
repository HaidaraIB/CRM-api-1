from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0024_add_lead_company_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="deal",
            name="reminder_date",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Follow-up reminder datetime for this deal",
            ),
        ),
    ]

