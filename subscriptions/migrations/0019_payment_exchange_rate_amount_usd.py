# Generated migration: store exchange rate at payment time and amount in USD

from decimal import Decimal
from django.db import migrations, models


def backfill_exchange_rate_and_amount_usd(apps, schema_editor):
    Payment = apps.get_model("subscriptions", "Payment")
    try:
        SystemSettings = apps.get_model("settings", "SystemSettings")
        settings_obj = SystemSettings.objects.filter().first()
        usd_to_iqd = float(settings_obj.usd_to_iqd_rate) if settings_obj and settings_obj.usd_to_iqd_rate else 1300.0
    except Exception:
        usd_to_iqd = 1300.0

    for p in Payment.objects.filter(amount_usd__isnull=True):
        if (p.currency or "USD").upper() == "IQD" and p.exchange_rate is None:
            rate = Decimal(str(usd_to_iqd))
            p.exchange_rate = rate
            p.amount_usd = (Decimal(str(p.amount)) / rate).quantize(Decimal("0.01"))
            p.save(update_fields=["exchange_rate", "amount_usd"])
        elif (p.currency or "USD").upper() == "USD":
            p.exchange_rate = Decimal("1")
            p.amount_usd = p.amount
            p.save(update_fields=["exchange_rate", "amount_usd"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0018_payment_currency"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="exchange_rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text="Rate at payment time (e.g. 1 USD = exchange_rate IQD). Null if USD.",
                max_digits=18,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="amount_usd",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Payment amount in USD (stored at payment time for reporting).",
                max_digits=10,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_exchange_rate_and_amount_usd, noop),
    ]
