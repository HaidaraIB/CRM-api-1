"""
Free-trial eligibility: one trial per company (typical SaaS).
"""
from __future__ import annotations

from subscriptions.models import Plan


def _is_plan_free(plan: Plan) -> bool:
    return float(plan.price_monthly or 0) <= 0 and float(plan.price_yearly or 0) <= 0


def is_free_trial_plan(plan: Plan) -> bool:
    """Free-priced plan with trial_days > 0 (time-limited trial)."""
    return _is_plan_free(plan) and int(getattr(plan, "trial_days", 0) or 0) > 0


def mark_company_free_trial_consumed(company_id: int) -> None:
    """Call when trial is forfeited or any paid invoice succeeds — no more free trials."""
    from companies.models import Company

    Company.objects.filter(pk=company_id, free_trial_consumed=False).update(
        free_trial_consumed=True
    )
