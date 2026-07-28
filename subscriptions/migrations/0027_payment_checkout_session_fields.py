# Generated manually for checkout session reuse fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0026_payment_applied_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="checkout_url",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="payment",
            name="session_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the gateway checkout session expires; retries may reuse until then.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="session_meta",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
