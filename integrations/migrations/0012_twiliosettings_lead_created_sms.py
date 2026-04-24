from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0011_messagetemplate_whatsapp_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="twiliosettings",
            name="lead_created_sms_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Send an automated SMS when a new lead (Client) is created.",
            ),
        ),
        migrations.AddField(
            model_name="twiliosettings",
            name="lead_created_sms_template",
            field=models.TextField(
                blank=True,
                default="Hello [first_name], we'll contact you soon!",
                help_text=(
                    "SMS body template for new leads. Placeholders: [name], [first_name], [phone], "
                    "[lead_company_name], [status], [company_name], [budget], [priority], [type], [source]."
                ),
            ),
        ),
    ]
