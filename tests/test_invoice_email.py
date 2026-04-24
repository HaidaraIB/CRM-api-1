"""Invoice email: subject, attachment, language."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.core.mail import get_connection
from django.utils import timezone

from subscriptions.invoicing.mailer import send_invoice_email
from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Subscription,
)
from settings.models import SMTPSettings


@pytest.mark.django_db
def test_send_invoice_email_subject_and_attachment(company, plan, settings, monkeypatch):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    monkeypatch.setattr("crm_saas_api.utils.get_smtp_connection", lambda: get_connection())

    smtp = SMTPSettings.get_settings()
    smtp.is_active = True
    smtp.from_email = "billing@example.com"
    smtp.save()

    gw = PaymentGateway.objects.create(
        name="GW Email Test",
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
        amount=Decimal("25.00"),
        currency="USD",
        amount_usd=Decimal("25.00"),
        payment_method=gw,
        payment_status=PaymentStatus.COMPLETED.value,
    )
    inv = pay.invoice
    assert inv is not None

    owner = company.owner
    assert owner and owner.email

    # Completed payment triggers payment_success signal email; only assert the invoice mail.
    n_before = len(mail.outbox)
    ok, msg = send_invoice_email(inv, to_email=owner.email, language="en")
    assert ok is True
    assert msg == "Sent"
    assert len(mail.outbox) == n_before + 1
    m = mail.outbox[-1]
    assert inv.invoice_number in m.subject
    assert company.name in m.subject
    assert len(m.attachments) == 1
    att_name, att_content, att_mime = m.attachments[0]
    assert att_name == f"{inv.invoice_number}.pdf"
    assert att_mime == "application/pdf"
    assert att_content.startswith(b"%PDF")


@pytest.mark.django_db
def test_send_invoice_email_arabic_template_for_owner_language(company, plan, settings, monkeypatch):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    monkeypatch.setattr("crm_saas_api.utils.get_smtp_connection", lambda: get_connection())

    smtp = SMTPSettings.get_settings()
    smtp.is_active = True
    smtp.from_email = "billing@example.com"
    smtp.save()

    owner = company.owner
    owner.language = "ar"
    owner.save(update_fields=["language"])

    gw = PaymentGateway.objects.create(
        name="GW Email AR",
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
        amount=Decimal("15.00"),
        currency="USD",
        amount_usd=Decimal("15.00"),
        payment_method=gw,
        payment_status=PaymentStatus.PENDING.value,
    )
    inv = pay.invoice

    ok, _ = send_invoice_email(inv, to_email=owner.email, language=None)
    assert ok is True
    body = mail.outbox[-1].alternatives[0][0]
    assert "فاتورة" in body


@pytest.mark.django_db
def test_send_invoice_email_explicit_en_override(company, plan, settings, monkeypatch):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    monkeypatch.setattr("crm_saas_api.utils.get_smtp_connection", lambda: get_connection())

    smtp = SMTPSettings.get_settings()
    smtp.is_active = True
    smtp.from_email = "billing@example.com"
    smtp.save()

    owner = company.owner
    owner.language = "ar"
    owner.save(update_fields=["language"])

    gw = PaymentGateway.objects.create(
        name="GW Email EN",
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
        amount=Decimal("20.00"),
        currency="USD",
        amount_usd=Decimal("20.00"),
        payment_method=gw,
        payment_status=PaymentStatus.COMPLETED.value,
    )
    inv = pay.invoice

    ok, _ = send_invoice_email(inv, to_email=owner.email, language="en")
    assert ok is True
    body = mail.outbox[-1].alternatives[0][0]
    assert "Thank you for your business" in body
