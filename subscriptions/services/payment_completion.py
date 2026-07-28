"""
Payment completion: gateway re-query before finalize, and checkout session reuse.

Never trust webhook/callback payload alone — always confirm with the gateway API
(or Stripe webhook signature) before marking COMPLETED / applying period rules.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from subscriptions.models import Payment, PaymentGateway, PaymentStatus, Plan, Subscription
from subscriptions.services.billing import finalize_completed_payment
from subscriptions.services.subscription_helpers import _payment_amount_usd

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TTL = timedelta(minutes=30)
AMOUNT_MATCH_TOLERANCE = Decimal("0.05")


def find_reusable_pending_payment(
    *,
    subscription: Subscription,
    gateway: PaymentGateway,
    target_plan: Plan,
    billing_cycle: str,
    amount_usd: Decimal | float,
) -> Optional[Payment]:
    """
    Return a still-valid PENDING payment for the same checkout intent, if any.
    Caller should return its checkout_url / session_meta instead of creating a new gateway session.
    """
    now = timezone.now()
    amount = Decimal(str(amount_usd))
    qs = (
        Payment.objects.filter(
            subscription=subscription,
            payment_method=gateway,
            payment_status=PaymentStatus.PENDING.value,
            target_plan=target_plan,
            billing_cycle=billing_cycle,
            session_expires_at__gt=now,
        )
        .order_by("-created_at")
    )
    for payment in qs:
        stored = payment.amount_usd if payment.amount_usd is not None else payment.amount
        if stored is None:
            continue
        if abs(Decimal(str(stored)) - amount) > AMOUNT_MATCH_TOLERANCE:
            continue
        if payment.checkout_url or payment.session_meta:
            return payment
    return None


def attach_checkout_session(
    payment: Payment,
    *,
    tran_ref: str,
    checkout_url: str = "",
    session_expires_at=None,
    session_meta: Optional[dict] = None,
) -> Payment:
    """Persist gateway session identifiers on the Payment row for reuse and callbacks."""
    payment.tran_ref = tran_ref or payment.tran_ref
    payment.checkout_url = checkout_url or ""
    payment.session_expires_at = session_expires_at or (timezone.now() + DEFAULT_SESSION_TTL)
    if session_meta is not None:
        payment.session_meta = session_meta
    payment.save(
        update_fields=[
            "tran_ref",
            "checkout_url",
            "session_expires_at",
            "session_meta",
            "updated_at",
        ]
    )
    return payment


def parse_gateway_expiry(value: Any):
    """Parse FIB validUntil (or similar) into aware datetime; fallback to default TTL."""
    if value is None:
        return timezone.now() + DEFAULT_SESSION_TTL
    if hasattr(value, "isoformat"):
        return value
    parsed = parse_datetime(str(value).replace("Z", "+00:00"))
    if parsed is None:
        return timezone.now() + DEFAULT_SESSION_TTL
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def _gateway_name(payment: Payment) -> str:
    gw = payment.payment_method
    return (gw.name or "").lower() if gw else ""


def is_gateway_payment_paid(payment: Payment) -> tuple[bool, Optional[dict]]:
    """
    Re-query the payment gateway. Returns (paid, raw_result).
    Does not mutate the Payment row.
    """
    if not payment.tran_ref:
        return False, None

    name = _gateway_name(payment)
    try:
        if "stripe" in name:
            from subscriptions.stripe_utils import verify_stripe_payment

            result = verify_stripe_payment(payment.tran_ref)
            paid = (
                result.get("stripe_payment_status") == "paid"
                or result.get("payment_status") == "completed"
            )
            return paid, result

        if "paytabs" in name:
            from subscriptions.paytabs_utils import verify_paytabs_payment

            result = verify_paytabs_payment(payment.tran_ref)
            status_code = (result.get("payment_result") or {}).get("response_status")
            return status_code == "A", result

        if "qicard" in name or "qi card" in name:
            from subscriptions.qicard_utils import verify_qicard_payment

            result = verify_qicard_payment(payment.tran_ref)
            status_val = (result.get("status") or "").upper()
            return status_val == "SUCCESS", result

        if "fib" in name or "first iraqi" in name:
            from subscriptions.fib_utils import check_fib_payment_status

            result = check_fib_payment_status(payment.tran_ref)
            return (result.get("status") or "").upper() == "PAID", result

        if "zain" in name:
            from subscriptions.zaincash_utils import check_zaincash_payment_status

            result = check_zaincash_payment_status(payment.tran_ref)
            return (result.get("status") or "").lower() == "success", result
    except Exception:
        logger.exception(
            "Gateway re-query failed payment_id=%s gateway=%s",
            payment.id,
            name,
        )
        return False, None

    logger.warning("Unknown gateway for payment_id=%s name=%s", payment.id, name)
    return False, None


@transaction.atomic
def confirm_and_finalize_payment(
    payment: Payment,
    *,
    mark_failed: bool = False,
) -> tuple[bool, str]:
    """
    Re-query gateway; if paid, mark COMPLETED and finalize. Returns (applied_or_already, reason).

    If mark_failed and gateway reports a hard failure, marks FAILED.
    """
    payment = Payment.objects.select_for_update().select_related(
        "subscription", "payment_method", "target_plan"
    ).get(pk=payment.pk)
    subscription = payment.subscription

    if payment.applied_at is not None:
        return True, "already_applied"

    if payment.payment_status == PaymentStatus.COMPLETED.value:
        finalize_completed_payment(subscription, payment, _payment_amount_usd(payment))
        return True, "finalized_existing_completed"

    paid, result = is_gateway_payment_paid(payment)
    if paid:
        payment.payment_status = PaymentStatus.COMPLETED.value
        payment.save(update_fields=["payment_status", "updated_at"])
        finalize_completed_payment(subscription, payment, _payment_amount_usd(payment))
        logger.info("Payment %s confirmed paid and finalized", payment.id)
        return True, "finalized"

    if mark_failed and result is not None:
        name = _gateway_name(payment)
        failed = False
        if "fib" in name:
            failed = (result.get("status") or "").upper() == "DECLINED"
        elif "qicard" in name or "qi card" in name:
            st = (result.get("status") or "").upper()
            failed = st in ("FAILED", "AUTHENTICATION_FAILED")
        if failed:
            payment.payment_status = PaymentStatus.FAILED.value
            payment.save(update_fields=["payment_status", "updated_at"])
            return False, "marked_failed"

    return False, "not_paid"


def find_payment_by_tran_ref(tran_ref: str) -> Optional[Payment]:
    if not tran_ref:
        return None
    return (
        Payment.objects.filter(tran_ref=tran_ref)
        .select_related("subscription", "payment_method", "target_plan")
        .order_by("-created_at")
        .first()
    )
