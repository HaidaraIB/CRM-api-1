"""
Create one read-only Invoice per Payment (idempotent).
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from subscriptions.invoicing.numbering import next_invoice_number

logger = logging.getLogger(__name__)


def _line_description(subscription, payment) -> str:
    plan = payment.target_plan or subscription.plan
    cycle = (payment.billing_cycle or subscription.billing_cycle or "").strip()
    name = plan.name if plan else "Subscription"
    if cycle:
        return f"{name} — {cycle}"
    return name


def ensure_invoice_for_payment(payment):
    """
    Idempotently create the invoice for this payment (exactly one per payment).
    Snapshots are taken once at creation and are not updated when payment status changes.
    """
    from subscriptions.models import Invoice

    if not payment.pk:
        return None

    existing = Invoice.objects.filter(payment_id=payment.pk).first()
    if existing:
        return existing

    try:
        subscription = payment.subscription
    except Exception:
        logger.warning("ensure_invoice_for_payment: missing subscription on payment %s", payment.pk)
        return None

    company = subscription.company
    plan = payment.target_plan or subscription.plan

    try:
        with transaction.atomic():
            year = timezone.now().year
            invoice_number = next_invoice_number(year)
            return Invoice.objects.create(
                payment=payment,
                subscription=subscription,
                invoice_number=invoice_number,
                amount=payment.amount,
                currency=(payment.currency or "USD").upper()[:10],
                company_name=(company.name if company else "")[:255],
                plan_name=(plan.name if plan else "")[:255],
                line_description=_line_description(subscription, payment)[:512],
                billing_cycle=(payment.billing_cycle or subscription.billing_cycle or "")[:10],
                due_date=timezone.now().date(),
            )
    except IntegrityError:
        return Invoice.objects.filter(payment_id=payment.pk).first()
    except Exception as e:
        logger.exception("ensure_invoice_for_payment failed: %s", e)
        return Invoice.objects.filter(payment_id=payment.pk).first()
