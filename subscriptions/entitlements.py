from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError

from subscriptions.entitlements_catalog import (
    DEFAULT_FEATURES,
    DEFAULT_USAGE_LIMITS_MONTHLY,
    normalize_bool,
)
from subscriptions.models import CompanyUsageCounter, Subscription


def _parse_unlimited_int(value: Any) -> Optional[int]:
    """
    Returns:
      - None for unlimited/empty
      - int for numeric values > 0 (0 is treated as 0)
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # avoid True/False becoming 1/0
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if not v or v in ("unlimited", "infinite", "∞", "none", "null", "no_limit", "nolimit"):
            return None
        # allow numeric strings
        try:
            return int(v)
        except ValueError:
            return None
    return None


def get_month_period_start(dt=None) -> date:
    dt = dt or timezone.now()
    # Use UTC date for consistency across workers
    if hasattr(dt, "date"):
        d = dt.date()
    else:
        d = dt
    return date(d.year, d.month, 1)


@dataclass(frozen=True)
class CompanyEntitlements:
    plan_id: Optional[int]
    plan_name: Optional[str]
    # quotas
    max_users: Optional[int]
    max_clients: Optional[int]
    extra_limits: dict[str, Any]
    # features
    features: dict[str, bool]
    # usage limits
    usage_limits_monthly: dict[str, Optional[int]]


def get_active_subscription(company) -> Optional[Subscription]:
    if not company:
        return None
    now = timezone.now()
    return (
        Subscription.objects.filter(company=company, is_active=True, end_date__gt=now)
        .select_related("plan")
        .order_by("-created_at")
        .first()
    )


def build_company_entitlements(company) -> CompanyEntitlements:
    sub = get_active_subscription(company)
    if not sub or not sub.plan:
        return CompanyEntitlements(
            plan_id=None,
            plan_name=None,
            max_users=None,
            max_clients=None,
            extra_limits={},
            features=dict(DEFAULT_FEATURES),
            usage_limits_monthly=dict(DEFAULT_USAGE_LIMITS_MONTHLY),
        )

    plan = sub.plan
    # Legacy quotas
    max_users = _parse_unlimited_int(getattr(plan, "users", None))
    max_clients = _parse_unlimited_int(getattr(plan, "clients", None))

    # Merge features with defaults
    raw_features = getattr(plan, "features", None) or {}
    features: dict[str, bool] = {}
    for k, default_val in DEFAULT_FEATURES.items():
        features[k] = normalize_bool(raw_features.get(k), default_val)

    # Merge usage limits with defaults (None means unlimited)
    raw_usage = getattr(plan, "usage_limits_monthly", None) or {}
    usage_limits: dict[str, Optional[int]] = {}
    for k, default_val in DEFAULT_USAGE_LIMITS_MONTHLY.items():
        usage_limits[k] = _parse_unlimited_int(raw_usage.get(k)) if raw_usage.get(k) is not None else default_val

    extra_limits = getattr(plan, "limits", None) or {}

    # Allow JSON limits to override legacy keys if explicitly provided
    if "max_users" in extra_limits:
        max_users = _parse_unlimited_int(extra_limits.get("max_users"))
    if "max_clients" in extra_limits:
        max_clients = _parse_unlimited_int(extra_limits.get("max_clients"))

    return CompanyEntitlements(
        plan_id=plan.id,
        plan_name=plan.name,
        max_users=max_users,
        max_clients=max_clients,
        extra_limits=extra_limits if isinstance(extra_limits, dict) else {},
        features=features,
        usage_limits_monthly=usage_limits,
    )


def require_feature(company, feature_key: str, *, message: str, error_key: str):
    ent = build_company_entitlements(company)
    allowed = bool(ent.features.get(feature_key, False))
    if not allowed:
        raise PermissionDenied(
            detail={
                "error": message,
                "error_key": error_key,
                "code": "FEATURE_NOT_AVAILABLE",
                "feature": feature_key,
                "plan_id": ent.plan_id,
            }
        )


def require_quota(company, quota_key: str, current_count: int, requested_delta: int = 1, *, message: str, error_key: str):
    ent = build_company_entitlements(company)
    limit = None
    if quota_key == "max_users":
        limit = ent.max_users
    elif quota_key == "max_clients":
        limit = ent.max_clients
    else:
        limit = _parse_unlimited_int(ent.extra_limits.get(quota_key))

    if limit is None:
        return
    if current_count + max(0, requested_delta) > limit:
        raise ValidationError(
            detail={
                "error": message,
                "error_key": error_key,
                "code": "QUOTA_EXCEEDED",
                "quota": quota_key,
                "limit": limit,
                "current": current_count,
                "requested_delta": requested_delta,
                "plan_id": ent.plan_id,
            },
            code=status.HTTP_403_FORBIDDEN,
        )


def require_monthly_usage(company, usage_key: str, requested_delta: int = 1, *, message: str, error_key: str):
    """
    Check monthly usage cap without incrementing.
    Call increment_monthly_usage(...) after the action succeeds.
    """
    ent = build_company_entitlements(company)
    limit = _parse_unlimited_int(ent.usage_limits_monthly.get(usage_key))
    if limit is None:
        return

    period_start = get_month_period_start()
    with transaction.atomic():
        row, _ = CompanyUsageCounter.objects.select_for_update().get_or_create(
            company=company,
            key=usage_key,
            period_start=period_start,
            defaults={"count": 0},
        )
        if row.count + max(0, requested_delta) > limit:
            raise ValidationError(
                detail={
                    "error": message,
                    "error_key": error_key,
                    "code": "USAGE_LIMIT_EXCEEDED",
                    "usage_key": usage_key,
                    "limit": limit,
                    "current": row.count,
                    "requested_delta": requested_delta,
                    "period_start": str(period_start),
                    "plan_id": ent.plan_id,
                },
                code=status.HTTP_403_FORBIDDEN,
            )


def increment_monthly_usage(company, usage_key: str, requested_delta: int = 1):
    if not company:
        return
    period_start = get_month_period_start()
    with transaction.atomic():
        row, _ = CompanyUsageCounter.objects.select_for_update().get_or_create(
            company=company,
            key=usage_key,
            period_start=period_start,
            defaults={"count": 0},
        )
        CompanyUsageCounter.objects.filter(pk=row.pk).update(
            count=F("count") + max(0, requested_delta)
        )


def get_monthly_usage_snapshot(company) -> dict[str, int]:
    """Return current month counters for common usage keys."""
    if not company:
        return {}
    period_start = get_month_period_start()
    qs = CompanyUsageCounter.objects.filter(company=company, period_start=period_start)
    return {r.key: int(r.count) for r in qs}

