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

from subscriptions.gateways.base import GatewayResult
from subscriptions.gateways.registry import adapter_for_payment
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


def query_gateway_state(payment: Payment) -> GatewayResult:
    """
    Re-query the payment gateway and return its normalized state.

    Does not mutate the Payment row. Never raises: a gateway that is down or
    unrecognised yields "unknown", which is treated as "do not act".
    """
    if not payment.tran_ref:
        return GatewayResult("unknown")

    adapter = adapter_for_payment(payment)
    if adapter is None:
        logger.warning(
            "Unknown gateway for payment_id=%s name=%s",
            payment.id,
            _gateway_name(payment),
        )
        return GatewayResult("unknown")

    try:
        return adapter.verify(payment.tran_ref)
    except Exception:
        logger.exception(
            "Gateway re-query failed payment_id=%s gateway=%s",
            payment.id,
            adapter.slug,
        )
        return GatewayResult("unknown")


def is_gateway_payment_paid(payment: Payment) -> tuple[bool, Optional[dict]]:
    """
    Back-compatible wrapper over query_gateway_state.

    Prefer query_gateway_state, which also distinguishes "failed" from "pending".
    """
    result = query_gateway_state(payment)
    if result.state == "unknown":
        return False, None
    return result.is_paid, result.raw


def _lock_payment_qs():
    """
    Row-lock the Payment table only.

    ``target_plan`` is nullable, so select_related() emits a LEFT OUTER JOIN.
    PostgreSQL rejects FOR UPDATE on the nullable side of an outer join
    (psycopg2 FeatureNotSupported). ``of=("self",)`` locks payments only.
    """
    return Payment.objects.select_for_update(of=("self",)).select_related(
        "subscription", "payment_method", "target_plan"
    )


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
    payment = _lock_payment_qs().get(pk=payment.pk)
    subscription = payment.subscription

    if payment.applied_at is not None:
        return True, "already_applied"

    if payment.payment_status == PaymentStatus.COMPLETED.value:
        finalize_completed_payment(subscription, payment, _payment_amount_usd(payment))
        return True, "finalized_existing_completed"

    result = query_gateway_state(payment)
    if result.is_paid:
        payment.payment_status = PaymentStatus.COMPLETED.value
        payment.save(update_fields=["payment_status", "updated_at"])
        finalize_completed_payment(subscription, payment, _payment_amount_usd(payment))
        logger.info("Payment %s confirmed paid and finalized", payment.id)
        return True, "finalized"

    # Each adapter maps its own status vocabulary onto "failed", so this works
    # for every gateway rather than only the two that used to be hard-coded.
    if mark_failed and result.is_failed:
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
