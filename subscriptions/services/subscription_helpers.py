"""Subscription domain helpers (single active subscription per company)."""
import logging
from datetime import timedelta

from django.utils import timezone

from ..models import Payment, PaymentStatus, Subscription

logger = logging.getLogger(__name__)

# Do not rewrite end_date when it already matches computed period within this tolerance.
_END_DATE_MATCH_TOLERANCE = timedelta(days=2)


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


def normalize_paid_subscription_end_date(subscription: Subscription) -> bool:
    """
    Align subscription.end_date with completed payment + paid plan when the DB was never
    updated by a gateway return (e.g. trial already active, polling check_payment only).

    - First completed payment: same base_date rules as Stripe (now or future start_date).
    - Prior completed payments: do not change a future end_date (renewals handled by gateways);
      only extend when end_date is in the past (reactivation).

    Returns True if end_date was saved.
    """
    subscription.refresh_from_db()
    plan = subscription.plan
    is_free_or_trial = float(plan.price_monthly) <= 0 and float(plan.price_yearly) <= 0
    if is_free_or_trial:
        return False

    latest = (
        Payment.objects.filter(
            subscription=subscription,
            payment_status=PaymentStatus.COMPLETED.value,
        )
        .order_by("-created_at")
        .first()
    )
    if not latest:
        return False

    amount_float = _payment_amount_usd(latest)
    if amount_float <= 0:
        return False

    billing_cycle = infer_billing_cycle_from_amount_usd(plan, amount_float)
    delta_days = 365 if billing_cycle == "yearly" else 30

    has_prior = (
        Payment.objects.filter(
            subscription=subscription,
            payment_status=PaymentStatus.COMPLETED.value,
        )
        .exclude(pk=latest.pk)
        .exists()
    )

    now = timezone.now()
    if has_prior:
        if subscription.end_date and subscription.end_date > now:
            return False
        base_date = now
    else:
        if subscription.start_date and subscription.start_date > now:
            base_date = subscription.start_date
        else:
            base_date = now

    new_end = base_date + timedelta(days=delta_days)
    if subscription.end_date is None:
        subscription.end_date = new_end
        subscription.save(update_fields=["end_date", "updated_at"])
        logger.info(
            "normalize_paid_subscription_end_date: set end_date subscription_id=%s",
            subscription.pk,
        )
        return True

    diff = new_end - subscription.end_date
    if -_END_DATE_MATCH_TOLERANCE <= diff <= _END_DATE_MATCH_TOLERANCE:
        return False

    subscription.end_date = new_end
    subscription.save(update_fields=["end_date", "updated_at"])
    logger.info(
        "normalize_paid_subscription_end_date: updated end_date subscription_id=%s",
        subscription.pk,
    )
    return True


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
