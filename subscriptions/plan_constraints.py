"""
Enforce at most one time-limited free trial plan and one free-forever plan (zero price, zero trial days).
"""
from __future__ import annotations

from django.core.exceptions import ValidationError

from subscriptions.models import Plan


def classify_plan_kind(
    price_monthly,
    price_yearly,
    trial_days,
) -> str:
    """Return 'paid' | 'free_trial' | 'free_forever'."""
    pm = float(price_monthly or 0)
    py = float(price_yearly or 0)
    td = int(trial_days or 0)
    if pm > 0 or py > 0:
        return "paid"
    if td > 0:
        return "free_trial"
    return "free_forever"


def validate_single_free_trial_and_free_forever_plans(
    *,
    price_monthly,
    price_yearly,
    trial_days,
    exclude_plan_id: int | None = None,
) -> None:
    """
    Raise ValidationError if another plan already occupies the same slot (trial vs free forever).
    """
    kind = classify_plan_kind(price_monthly, price_yearly, trial_days)
    if kind == "paid":
        return

    qs = Plan.objects.all()
    if exclude_plan_id is not None:
        qs = qs.exclude(pk=exclude_plan_id)

    for other in qs:
        ok = classify_plan_kind(
            other.price_monthly,
            other.price_yearly,
            other.trial_days,
        )
        if kind == ok:
            if kind == "free_trial":
                raise ValidationError(
                    "Only one free trial plan is allowed (zero price and trial_days > 0). "
                    "Edit the existing trial plan or convert it to paid/free first.",
                    code="duplicate_free_trial_plan",
                )
            raise ValidationError(
                "Only one free plan is allowed (zero price and trial_days = 0). "
                "Edit the existing free plan or add prices to create a paid plan.",
                code="duplicate_free_forever_plan",
            )


def validate_plan_instance_uniqueness(plan: Plan, exclude_self: bool = True) -> None:
    validate_single_free_trial_and_free_forever_plans(
        price_monthly=plan.price_monthly,
        price_yearly=plan.price_yearly,
        trial_days=plan.trial_days,
        exclude_plan_id=plan.pk if exclude_self and plan.pk else None,
    )
