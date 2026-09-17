"""
Launch trial codes: validate and redeem admin-issued access codes.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from subscriptions.cache_utils import invalidate_company_subscription_cache
from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
    TrialCode,
    TrialCodeRedemption,
)
from subscriptions.services.subscription_helpers import deactivate_other_subscriptions_for_company

CODE_RE = re.compile(r"^[A-Z0-9_-]{4,32}$")
GENERATE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class TrialCodeError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.error_code = code
        self.message = message or code
        super().__init__(self.message)


def normalize_trial_code(raw: str) -> str:
    return (raw or "").strip().upper()


def is_paid_plan(plan: Plan) -> bool:
    return float(plan.price_monthly or 0) > 0 or float(plan.price_yearly or 0) > 0


def validate_trial_code_format(code: str) -> None:
    if not code or not CODE_RE.match(code):
        raise TrialCodeError("invalid_code", "Invalid trial code format.")


def generate_unique_code(length: int = 8) -> str:
    while True:
        code = "".join(secrets.choice(GENERATE_ALPHABET) for _ in range(length))
        if not TrialCode.objects.filter(code=code).exists():
            return code


@dataclass
class ValidatedTrialCode:
    trial_code: TrialCode
    plan: Plan


def get_trial_code_for_validation(raw_code: str) -> TrialCode:
    code = normalize_trial_code(raw_code)
    validate_trial_code_format(code)
    try:
        return TrialCode.objects.select_related("plan").get(code=code)
    except TrialCode.DoesNotExist:
        raise TrialCodeError("invalid_code", "Trial code not found.")


def validate_trial_code_state(trial_code: TrialCode) -> Plan:
    if not trial_code.is_active:
        raise TrialCodeError("code_inactive", "Trial code is not active.")
    now = timezone.now()
    if trial_code.starts_at and now < trial_code.starts_at:
        raise TrialCodeError("code_not_started", "Trial code is not yet valid.")
    if trial_code.expires_at and now > trial_code.expires_at:
        raise TrialCodeError("code_expired", "Trial code has expired.")
    if trial_code.redeemed_count >= trial_code.max_redemptions:
        raise TrialCodeError("code_exhausted", "Trial code has reached its redemption limit.")
    plan = trial_code.plan
    if not plan or not is_paid_plan(plan):
        raise TrialCodeError("invalid_code", "Trial code plan is not valid.")
    return plan


def validate_trial_code(raw_code: str) -> ValidatedTrialCode:
    trial_code = get_trial_code_for_validation(raw_code)
    plan = validate_trial_code_state(trial_code)
    return ValidatedTrialCode(trial_code=trial_code, plan=plan)


def _has_completed_payment(subscription: Subscription) -> bool:
    return Payment.objects.filter(
        subscription=subscription,
        payment_status=PaymentStatus.COMPLETED.value,
    ).exists()


def _get_current_subscription(company) -> Optional[Subscription]:
    sub = (
        Subscription.objects.filter(company=company, is_active=True)
        .order_by("-created_at")
        .first()
    )
    if sub:
        return sub
    return Subscription.objects.filter(company=company).order_by("-created_at").first()


def company_can_redeem_trial_code(company) -> Tuple[bool, Optional[str]]:
    subscription = _get_current_subscription(company)
    if subscription is None:
        return True, None
    if subscription.is_truly_active():
        return False, "not_eligible"
    if _has_completed_payment(subscription):
        return False, "not_eligible"
    return True, None


@transaction.atomic
def redeem_trial_code(
    *,
    raw_code: str,
    company,
    owner=None,
    subscription: Optional[Subscription] = None,
) -> Subscription:
    validated = validate_trial_code(raw_code)
    trial_code = (
        TrialCode.objects.select_for_update()
        .select_related("plan")
        .get(pk=validated.trial_code.pk)
    )
    plan = validate_trial_code_state(trial_code)

    eligible, reason = company_can_redeem_trial_code(company)
    if not eligible:
        raise TrialCodeError(
            reason or "not_eligible",
            "Company is not eligible to redeem this code.",
        )

    if TrialCodeRedemption.objects.filter(code=trial_code, company=company).exists():
        raise TrialCodeError(
            "already_redeemed",
            "This company has already redeemed this code.",
        )

    if trial_code.redeemed_count >= trial_code.max_redemptions:
        raise TrialCodeError(
            "code_exhausted",
            "Trial code has reached its redemption limit.",
        )

    now = timezone.now()
    trial_days = int(trial_code.trial_days)
    end_date = now + timedelta(days=trial_days)

    if subscription is None:
        subscription = _get_current_subscription(company)

    if subscription is None:
        subscription = Subscription.objects.create(
            company=company,
            plan=plan,
            end_date=end_date,
            is_active=True,
            subscription_status=SubscriptionStatus.TRIALING,
            current_period_start=now,
            billing_cycle=BillingCycle.MONTHLY,
            trial_code=trial_code,
        )
    else:
        deactivate_other_subscriptions_for_company(
            company.id, exclude_subscription_id=subscription.id
        )
        subscription.plan = plan
        subscription.is_active = True
        subscription.subscription_status = SubscriptionStatus.TRIALING
        subscription.current_period_start = now
        subscription.end_date = end_date
        subscription.auto_renew = False
        subscription.pending_plan = None
        subscription.pending_billing_cycle = None
        subscription.trial_code = trial_code
        subscription.save(
            update_fields=[
                "plan",
                "is_active",
                "subscription_status",
                "current_period_start",
                "end_date",
                "auto_renew",
                "pending_plan",
                "pending_billing_cycle",
                "trial_code",
                "updated_at",
            ]
        )

    owner_email = ""
    owner_name = ""
    if owner is not None:
        owner_email = getattr(owner, "email", "") or ""
        owner_name = (
            f"{getattr(owner, 'first_name', '')} {getattr(owner, 'last_name', '')}".strip()
        )
    elif getattr(company, "owner_id", None):
        o = company.owner
        owner_email = getattr(o, "email", "") or ""
        owner_name = f"{getattr(o, 'first_name', '')} {getattr(o, 'last_name', '')}".strip()

    TrialCodeRedemption.objects.create(
        code=trial_code,
        company=company,
        subscription=subscription,
        trial_days=trial_days,
        plan_id_snapshot=plan.id,
        trial_ends_at=end_date,
        owner_email=owner_email,
        owner_name=owner_name,
    )
    trial_code.redeemed_count += 1
    trial_code.save(update_fields=["redeemed_count", "updated_at"])

    invalidate_company_subscription_cache(company.id)
    return subscription
