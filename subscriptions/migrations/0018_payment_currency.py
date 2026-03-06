# Generated migration for Payment.currency

from django.db import migrations, models
from django.db.models import Q


def set_currency_for_iqd_payments(apps, schema_editor):
    """Set currency=IQD for existing payments from IQD gateways (PayTabs, Zain Cash) where amount looks like IQD."""
    Payment = apps.get_model("subscriptions", "Payment")
    PaymentGateway = apps.get_model("subscriptions", "PaymentGateway")
    iqd_gateway_ids = list(
        PaymentGateway.objects.filter(
            Q(name__icontains="paytabs") | Q(name__icontains="zaincash") | Q(name__icontains="zain cash")
        ).values_list("id", flat=True)
    )
    if not iqd_gateway_ids:
        return
    # Payments from these gateways with amount > 1000 are likely IQD (plan prices in USD are typically < 1000)
    updated = Payment.objects.filter(
        payment_method_id__in=iqd_gateway_ids,
        amount__gt=1000,
    ).update(currency="IQD")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0017_remove_broadcast_target"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="currency",
            field=models.CharField(default="USD", help_text="ISO currency code (USD, IQD, etc.)", max_length=10),
        ),
        migrations.RunPython(set_currency_for_iqd_payments, noop),
    ]
