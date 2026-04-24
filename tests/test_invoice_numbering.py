"""Invoice sequence and ensure_invoice_for_payment."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.utils import timezone

from subscriptions.invoicing.numbering import next_invoice_number
from subscriptions.invoicing.snapshot import ensure_invoice_for_payment
from subscriptions.models import (
    BillingCycle,
    Invoice,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Subscription,
)


@pytest.mark.django_db
def test_next_invoice_number_monotonic():
    y = 2099
    Invoice.objects.filter(invoice_number__startswith=f"INV-{y}-").delete()
    from subscriptions.models import InvoiceSequence

    InvoiceSequence.objects.filter(year=y).delete()

    with transaction.atomic():
        a = next_invoice_number(y)
        b = next_invoice_number(y)
    assert a == "INV-2099-00001"
    assert b == "INV-2099-00002"


@pytest.mark.django_db
def test_ensure_invoice_for_payment_idempotent(company, plan):
    gw = PaymentGateway.objects.create(
        name="GW Invoice Test",
        status=PaymentGatewayStatus.ACTIVE.value,
        enabled=True,
    )
    now = timezone.now()
    sub = Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        start_date=now,
        end_date=now + timedelta(days=30),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
    )
    pay = Payment.objects.create(
        subscription=sub,
        amount=Decimal("10.00"),
        currency="USD",
        amount_usd=Decimal("10.00"),
        payment_method=gw,
        payment_status=PaymentStatus.COMPLETED.value,
    )
    inv1 = ensure_invoice_for_payment(pay)
    inv2 = ensure_invoice_for_payment(pay)
    assert inv1 is not None and inv2 is not None
    assert inv1.pk == inv2.pk
    assert Invoice.objects.filter(payment=pay).count() == 1
