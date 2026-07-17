from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0043_pbx_integration"),
    ]

    operations = [
        migrations.AlterField(
            model_name="client",
            name="phone_number",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name="client",
            name="meta_leadgen_id",
            field=models.CharField(
                blank=True,
                help_text="Meta leadgen_id from Lead Ads webhook (15-17 digits) for Conversion Leads CAPI",
                max_length=64,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="clientphonenumber",
            name="phone_number",
            field=models.CharField(help_text="The phone number", max_length=64),
        ),
    ]
