"""Subscription domain helpers (single active subscription per company)."""
import logging

from ..models import Payment, PaymentStatus, Subscription

logger = logging.getLogger(__name__)


def infer_billing_cycle_from_amount_usd(plan, amount_usd: float) -> str:
    """
    Match Stripe/PayTabs: pick yearly vs monthly from paid amount vs plan prices.
    """
    if amount_usd <= 0:
        return "monthly"
    pm = float(plan.price_monthly)
    py = float(plan.price_yearly)
    if abs(amount_usd - py) < 0.01:
        return "yearly"
    if abs(amount_usd - pm) < 0.01:
        return "monthly"
    yearly_diff = abs(amount_usd - py)
    monthly_diff = abs(amount_usd - pm)
    return "yearly" if yearly_diff < monthly_diff else "monthly"


def _payment_amount_usd(payment: Payment) -> float:
    if payment.amount_usd is not None:
        return float(payment.amount_usd)
    cur = (payment.currency or "USD").upper()
    if cur == "USD":
        return float(payment.amount)
    return 0.0


def reconcile_unapplied_completed_payment(subscription: Subscription) -> bool:
    """
    If the latest completed payment was never applied to the subscription window,
    run finalize_completed_payment (same rules as gateway handlers: initial / renewal / upgrade).

    Poll-safe and idempotent via Payment.applied_at — never invents now+period.

    Returns True if finalize applied a payment.
    """
    from .billing import finalize_completed_payment

    subscription.refresh_from_db()
    plan = subscription.plan
    is_free_or_trial = float(plan.price_monthly) <= 0 and float(plan.price_yearly) <= 0

    latest = (
        Payment.objects.filter(
            subscription=subscription,
            payment_status=PaymentStatus.COMPLETED.value,
            applied_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if not latest:
        return False

    # Paid checkout on a free/trial plan row is still applied (upgrade path).
    # Skip only when there is nothing meaningful to apply.
    amount_float = _payment_amount_usd(latest)
    if amount_float <= 0 and is_free_or_trial:
        return False
    if amount_float <= 0:
        return False

    finalize_completed_payment(subscription, latest, amount_float)
    latest.refresh_from_db()

    if latest.applied_at is None:
        return False

    logger.info(
        "reconcile_unapplied_completed_payment: applied payment_id=%s subscription_id=%s",
        latest.pk,
        subscription.pk,
    )
    return True


def normalize_paid_subscription_end_date(subscription: Subscription) -> bool:
    """Deprecated alias for reconcile_unapplied_completed_payment."""
    return reconcile_unapplied_completed_payment(subscription)


def deactivate_other_subscriptions_for_company(company_id, exclude_subscription_id=None):
    """Ensure only one subscription per company is active: deactivate all others for this company."""
    qs = Subscription.objects.filter(company_id=company_id)
    if exclude_subscription_id is not None:
        qs = qs.exclude(pk=exclude_subscription_id)
    updated = qs.filter(is_active=True).update(is_active=False)
    if updated:
        logger.info(
            "Deactivated %s other subscription(s) for company_id=%s (only one active per company)",
            updated,
            company_id,
        )
