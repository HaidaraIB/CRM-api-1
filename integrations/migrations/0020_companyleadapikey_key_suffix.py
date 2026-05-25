from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0019_company_lead_api_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="companyleadapikey",
            name="key_suffix",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Last characters of the key for display in the UI",
                max_length=8,
            ),
        ),
    ]
