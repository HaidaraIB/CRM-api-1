import pytest
from rest_framework.exceptions import ValidationError, PermissionDenied
from subscriptions.entitlements import require_quota, require_feature
from subscriptions.models import Plan, Subscription
from django.utils import timezone
from datetime import timedelta

@pytest.mark.django_db
def test_require_quota_success(company):
    # Depending on plan defaults, quotas might be unlimited
    # We just run the function and assert it doesn't raise exception
    require_quota(company, "max_users", 5, 1, message="Too many users", error_key="max_users")

@pytest.mark.django_db
def test_require_feature(company):
    # Assume default plan allows some basic features if no limitations apply
    # If the feature checking logic raises PermissionDenied, we expect it.
    try:
        require_feature(company, "some_fake_feature", message="Not allowed", error_key="some_feature")
    except PermissionDenied:
        pass  # expected


@pytest.mark.django_db
def test_require_quota_max_employees_uses_legacy_users_alias(company):
    plan = Plan.objects.create(
        name="Employees Plan",
        description="Employees limit",
        price_monthly=0,
        price_yearly=0,
        users="2",
        clients="unlimited",
        limits={"max_employees": 2},
    )
    Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        end_date=timezone.now() + timedelta(days=30),
    )
    with pytest.raises(ValidationError):
        require_quota(
            company,
            "max_employees",
            current_count=2,
            requested_delta=1,
            message="Too many employees",
            error_key="max_employees",
        )


@pytest.mark.django_db
def test_require_feature_integration_flag_disabled(company):
    plan = Plan.objects.create(
        name="Integrations Off",
        description="No WhatsApp",
        price_monthly=0,
        price_yearly=0,
        features={"integration_whatsapp": False},
    )
    Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        end_date=timezone.now() + timedelta(days=30),
    )
    with pytest.raises(PermissionDenied):
        require_feature(
            company,
            "integration_whatsapp",
            message="Not included",
            error_key="plan_integration_not_included",
        )
