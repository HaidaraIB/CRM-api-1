"""
Central billing logic: proration, checkout pricing, applying successful payments.

Field semantics (Stripe-like):
- start_date: set once at Subscription creation; do not rewrite on plan changes.
- current_period_start / end_date: current billing window; proration uses the remaining
  fraction of (end_date - current_period_start).
- Upgrades: charge proration; period end unchanged.
- Renewals: new window [old end_date, old end_date + period).
- Downgrades / paid→free: scheduled via pending_plan; applied at period boundary by
  end_expired_subscriptions (or switch-plan-free for immediate free-only switches).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from subscriptions.cache_utils import invalidate_company_subscription_cache
from subscriptions.services.trial_eligibility import mark_company_free_trial_consumed
from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
)

logger = logging.getLogger(__name__)

# Match legacy gateway behaviour (30-day months, 365-day years).
PERIOD_DAYS_MONTHLY = 30
PERIOD_DAYS_YEARLY = 365

AMOUNT_TOLERANCE_USD = Decimal("0.05")
AMOUNT_TOLERANCE_RATIO = Decimal("0.02")


def is_plan_free(plan: Plan) -> bool:
    return float(plan.price_monthly or 0) <= 0 and float(plan.price_yearly or 0) <= 0


def plan_price_for_cycle(plan: Plan, billing_cycle: str) -> float:
    if billing_cycle == BillingCycle.YEARLY:
        return float(plan.price_yearly or 0)
    return float(plan.price_monthly or 0)


def period_days_for_cycle(billing_cycle: str) -> int:
    return PERIOD_DAYS_YEARLY if billing_cycle == BillingCycle.YEARLY else PERIOD_DAYS_MONTHLY


def subscription_period_length_days(subscription: Subscription) -> int:
    """Length of current billing period in days (for proration denominator)."""
    if subscription.current_period_start and subscription.end_date:
        delta = subscription.end_date - subscription.current_period_start
        days = max(1, int(delta.total_seconds() // 86400))
        return days
    return period_days_for_cycle(subscription.billing_cycle or BillingCycle.MONTHLY)


def remaining_seconds_in_period(subscription: Subscription, as_of=None) -> float:
    as_of = as_of or timezone.now()
    if not subscription.end_date:
        return 0.0
    return max(0.0, (subscription.end_date - as_of).total_seconds())


def compute_upgrade_proration_usd(
    subscription: Subscription,
    old_plan: Plan,
    new_plan: Plan,
    billing_cycle: str,
    as_of=None,
) -> Tuple[Decimal, int, int]:
    """
    Prorated upgrade amount in USD: (P_new - P_old) * (remaining / period_length).
    Returns (amount_usd, remaining_whole_days, period_days).
    """
    as_of = as_of or timezone.now()
    old_p = plan_price_for_cycle(old_plan, billing_cycle)
    new_p = plan_price_for_cycle(new_plan, billing_cycle)
    period_days = subscription_period_length_days(subscription)
    remaining_sec = remaining_seconds_in_period(subscription, as_of)
    remaining_days = max(0, int(remaining_sec // 86400))
    if remaining_sec > 0 and remaining_days == 0:
        remaining_days = 1
    diff = new_p - old_p
    if diff <= 0 or period_days <= 0:
        return Decimal("0"), remaining_days, period_days
    ratio = min(1.0, remaining_sec / float(period_days * 86400))
    amount = Decimal(str(diff * ratio))
    return amount.quantize(Decimal("0.01")), remaining_days, period_days


def _amount_matches_expected(paid: float, expected: Decimal) -> bool:
    p = Decimal(str(paid))
    exp = expected.quantize(Decimal("0.01"))
    if exp == 0:
        return p <= AMOUNT_TOLERANCE_USD
    diff = abs(p - exp)
    return diff <= AMOUNT_TOLERANCE_USD or diff / max(exp, Decimal("0.01")) <= AMOUNT_TOLERANCE_RATIO


def normalize_subscription_billing_window(subscription: Subscription) -> list[str]:
    """
    Ensure current_period_start and end_date form a valid billing window (Stripe-style:
    current_period_start < end_date). Immutable subscription start_date is not touched.

    If current_period_start is missing, infer it from end_date and billing_cycle, clamped
    to subscription.start_date when that anchor is later (mid-period signup).
    """
    if not subscription.end_date:
        return []
    bc = subscription.billing_cycle or BillingCycle.MONTHLY
    pd = period_days_for_cycle(bc)
    updated: list[str] = []

    def _clamp_start(inferred):
        if subscription.start_date and inferred < subscription.start_date:
            return subscription.start_date
        return inferred

    if not subscription.current_period_start:
        inferred = _clamp_start(subscription.end_date - timedelta(days=pd))
        subscription.current_period_start = inferred
        updated.append("current_period_start")
    elif subscription.current_period_start >= subscription.end_date:
        inferred = _clamp_start(subscription.end_date - timedelta(days=pd))
        subscription.current_period_start = inferred
        updated.append("current_period_start")
    return updated


def _prior_completed_payments_exist(subscription_id: int, exclude_payment_id: Optional[int]) -> bool:
    qs = Payment.objects.filter(
        subscription_id=subscription_id,
        payment_status=PaymentStatus.COMPLETED.value,
    )
    if exclude_payment_id is not None:
        qs = qs.exclude(pk=exclude_payment_id)
    return qs.exists()


@transaction.atomic
def apply_successful_payment(
    subscription: Subscription,
    *,
    amount_usd: float,
    target_plan: Plan,
    billing_cycle: str,
    exclude_payment_id: Optional[int] = None,
) -> Subscription:
    """
    Apply a completed payment: set plan, period dates, and status.
    Call from gateway return handlers after verifying gateway state.
    """
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    target_plan = Plan.objects.get(pk=target_plan.pk)
    now = timezone.now()
    old_plan = Plan.objects.get(pk=subscription.plan_id)

    norm_fields = normalize_subscription_billing_window(subscription)
    if norm_fields:
        subscription.save(update_fields=norm_fields + ["updated_at"])

    prior_completed = _prior_completed_payments_exist(subscription.id, exclude_payment_id)
    period_days = period_days_for_cycle(billing_cycle)
    new_price = plan_price_for_cycle(target_plan, billing_cycle)

    # --- Upgrade: higher tier, active period, both paid in this cycle ---
    if (
        prior_completed
        and subscription.end_date
        and subscription.end_date > now
        and not is_plan_free(old_plan)
        and not is_plan_free(target_plan)
        and target_plan.tier > old_plan.tier
    ):
        expected = compute_upgrade_proration_usd(
            subscription, old_plan, target_plan, billing_cycle, as_of=now
        )[0]
        if not _amount_matches_expected(amount_usd, expected):
            logger.warning(
                "Upgrade proration mismatch sub=%s paid=%s expected=%s",
                subscription.id,
                amount_usd,
                expected,
            )
            raise ValueError(
                f"Payment amount does not match prorated upgrade (expected ~{expected} USD)."
            )
        subscription.plan = target_plan
        subscription.billing_cycle = billing_cycle
        subscription.subscription_status = SubscriptionStatus.ACTIVE
        subscription.is_active = True
        subscription.save(
            update_fields=[
                "plan",
                "billing_cycle",
                "subscription_status",
                "is_active",
                "updated_at",
            ]
        )
        invalidate_company_subscription_cache(subscription.company_id)
        mark_company_free_trial_consumed(subscription.company_id)
        return subscription

    # --- Renewal: same plan, extend from current period end ---
    if (
        prior_completed
        and target_plan.id == old_plan.id
        and subscription.end_date
        and subscription.end_date > now
    ):
        full = Decimal(str(new_price))
        if not _amount_matches_expected(amount_usd, full):
            raise ValueError(
                f"Payment amount does not match renewal price (expected ~{full} USD)."
            )
        base = subscription.end_date
        subscription.current_period_start = base
        subscription.end_date = base + timedelta(days=period_days)
        subscription.billing_cycle = billing_cycle
        subscription.subscription_status = SubscriptionStatus.ACTIVE
        subscription.is_active = True
        subscription.save(
            update_fields=[
                "current_period_start",
                "end_date",
                "billing_cycle",
                "subscription_status",
                "is_active",
                "updated_at",
            ]
        )
        invalidate_company_subscription_cache(subscription.company_id)
        mark_company_free_trial_consumed(subscription.company_id)
        return subscription

    # --- Initial purchase / reactivation / trial→paid ---
    if not _amount_matches_expected(amount_usd, Decimal(str(new_price))):
        raise ValueError(
            f"Payment amount does not match plan price for cycle (expected ~{new_price} USD)."
        )
    # New paid period starts when payment succeeds (aligned with major billing providers).
    base = now
    subscription.plan = target_plan
    subscription.current_period_start = base
    subscription.end_date = base + timedelta(days=period_days)
    subscription.billing_cycle = billing_cycle
    subscription.subscription_status = (
        SubscriptionStatus.TRIALING
        if is_plan_free(target_plan) and int(getattr(target_plan, "trial_days", 0) or 0) > 0
        else SubscriptionStatus.ACTIVE
    )
    subscription.is_active = True
    subscription.pending_plan = None
    subscription.pending_billing_cycle = None
    subscription.save(
        update_fields=[
            "plan",
            "current_period_start",
            "end_date",
            "billing_cycle",
            "subscription_status",
            "is_active",
            "pending_plan",
            "pending_billing_cycle",
            "updated_at",
        ]
    )
    invalidate_company_subscription_cache(subscription.company_id)
    mark_company_free_trial_consumed(subscription.company_id)
    return subscription


def resolve_checkout_pricing(
    subscription: Subscription,
    *,
    target_plan_id: Optional[int],
    billing_cycle_param: Optional[str],
) -> Tuple[Plan, str, Decimal, str]:
    """
    Decide target plan, billing cycle, amount (USD), and intent label for a new checkout session.
    Does not mutate the subscription or plan rows.

    Returns:
        (target_plan, billing_cycle, amount_usd, intent)
        intent: initial | renewal | upgrade | error
    """
    subscription = Subscription.objects.select_related("plan").get(pk=subscription.pk)
    old_plan = subscription.plan
    if target_plan_id:
        target_plan = Plan.objects.get(pk=target_plan_id)
    else:
        target_plan = old_plan

    if billing_cycle_param in (BillingCycle.MONTHLY, BillingCycle.YEARLY):
        billing_cycle = billing_cycle_param
    elif subscription.billing_cycle:
        billing_cycle = subscription.billing_cycle
    else:
        days_diff = (subscription.end_date - subscription.start_date).days
        billing_cycle = BillingCycle.YEARLY if days_diff >= 330 else BillingCycle.MONTHLY

    now = timezone.now()
    prior_completed = _prior_completed_payments_exist(subscription.id, None)

    # Downgrade / same-tier lateral: must schedule, not checkout
    if target_plan.tier < old_plan.tier:
        raise ValueError(
            "Downgrades are scheduled for the end of the billing period. "
            "Use the schedule-change endpoint instead of payment checkout."
        )
    if target_plan.tier == old_plan.tier and target_plan.id != old_plan.id:
        raise ValueError("Lateral plan changes are not supported via checkout.")

    # Upgrade
    if (
        prior_completed
        and subscription.end_date
        and subscription.end_date > now
        and not is_plan_free(old_plan)
        and not is_plan_free(target_plan)
        and target_plan.tier > old_plan.tier
    ):
        amt, _, _ = compute_upgrade_proration_usd(
            subscription, old_plan, target_plan, billing_cycle, as_of=now
        )
        if amt <= 0:
            raise ValueError("No prorated amount due for this upgrade.")
        return target_plan, billing_cycle, amt, "upgrade"

    # Renewal (same plan)
    if prior_completed and target_plan.id == old_plan.id and subscription.end_date and subscription.end_date > now:
        price = Decimal(str(plan_price_for_cycle(target_plan, billing_cycle)))
        return target_plan, billing_cycle, price, "renewal"

    # First payment / trial→paid / expired reactivation
    price = Decimal(str(plan_price_for_cycle(target_plan, billing_cycle)))
    if price <= 0:
        raise ValueError("Target plan does not require payment for this billing cycle.")
    return target_plan, billing_cycle, price, "initial"


def finalize_completed_payment(
    subscription: Subscription,
    payment: Payment,
    amount_usd: float,
) -> Subscription:
    """
    Load target plan / billing cycle from the Payment row and apply successful payment rules.
    Use this from gateway return handlers after marking the payment completed.
    """
    from subscriptions.services.subscription_helpers import infer_billing_cycle_from_amount_usd

    target = payment.target_plan or subscription.plan
    bc = payment.billing_cycle
    if not bc:
        bc = infer_billing_cycle_from_amount_usd(target, amount_usd)
    return apply_successful_payment(
        subscription,
        amount_usd=amount_usd,
        target_plan=target,
        billing_cycle=bc,
        exclude_payment_id=payment.id,
    )


def preview_plan_change(
    subscription: Subscription,
    *,
    plan_id: int,
    billing_cycle_param: Optional[str],
) -> dict[str, Any]:
    """Data for GET preview-change (no mutation)."""
    try:
        target_plan, billing_cycle, amount_usd, intent = resolve_checkout_pricing(
            subscription,
            target_plan_id=plan_id,
            billing_cycle_param=billing_cycle_param,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e), "code": "invalid_change"}

    now = timezone.now()
    old_plan = subscription.plan
    sub = subscription
    days_left = 0
    if sub.end_date:
        days_left = max(0, (sub.end_date - now).days)

    return {
        "ok": True,
        "intent": intent,
        "billing_cycle": billing_cycle,
        "amount_usd": str(amount_usd),
        "current_period_end": sub.end_date.isoformat() if sub.end_date else None,
        "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "days_remaining_in_period": days_left,
        "from_plan": {"id": old_plan.id, "name": old_plan.name, "tier": old_plan.tier},
        "to_plan": {"id": target_plan.id, "name": target_plan.name, "tier": target_plan.tier},
    }
