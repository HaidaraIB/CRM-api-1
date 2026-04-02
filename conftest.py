"""
Shared pytest fixtures for the CRM API test suite.
"""
import json

import pytest
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def _tests_skip_api_key_gate(settings):
    """
    APIKeyValidationMiddleware skips validation when no keys are configured.
    Clear keys so local .env does not force X-API-Key on every request.
    """
    settings.API_KEY_MOBILE = ""
    settings.API_KEY_WEB = ""
    settings.API_KEY_ADMIN = ""


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def owner_user(db):
    """User who owns the company FK; company is set after Company row exists."""
    from accounts.models import User

    return User.objects.create_user(
        username="owner_user",
        email="owner@test.com",
        password="testpass123",
        first_name="Owner",
        last_name="User",
        company=None,
        role="admin",
    )


@pytest.fixture
def company(owner_user, db):
    from companies.models import Company

    c = Company.objects.create(
        name="Test Company",
        domain="test-company.example.com",
        owner=owner_user,
    )
    owner_user.company = c
    owner_user.save(update_fields=["company"])
    return c


@pytest.fixture
def other_owner_user(db):
    from accounts.models import User

    return User.objects.create_user(
        username="other_owner",
        email="otherowner@test.com",
        password="testpass123",
        first_name="Other",
        last_name="Owner",
        company=None,
        role="admin",
    )


@pytest.fixture
def other_company(other_owner_user, db):
    from companies.models import Company

    c = Company.objects.create(
        name="Other Company",
        domain="other-company.example.com",
        owner=other_owner_user,
    )
    other_owner_user.company = c
    other_owner_user.save(update_fields=["company"])
    return c


@pytest.fixture
def plan(db):
    from subscriptions.models import Plan
    from decimal import Decimal
    return Plan.objects.create(
        name="Pro",
        description="Test plan",
        price_monthly=Decimal("49.99"),
        price_yearly=Decimal("499.99"),
    )


def api_body(response):
    """Return the inner payload for tests (unwraps { success, data } from EnvelopeJSONRenderer)."""
    d = getattr(response, "data", None)
    if d is None:
        d = json.loads(response.content.decode())
    if isinstance(d, dict) and d.get("success") is True and "data" in d:
        return d["data"]
    return d


@pytest.fixture
def subscription(company, plan, db):
    from subscriptions.models import BillingCycle, Subscription
    now = timezone.now()
    return Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        start_date=now,
        end_date=now + timedelta(days=30),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
    )


@pytest.fixture
def expired_subscription(company, plan, db):
    from subscriptions.models import Subscription
    return Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        start_date=timezone.now() - timedelta(days=60),
        end_date=timezone.now() - timedelta(days=1),
    )


@pytest.fixture
def admin_user(company, db):
    from accounts.models import User
    return User.objects.create_user(
        username="admin_user",
        email="admin@test.com",
        password="testpass123",
        first_name="Admin",
        last_name="User",
        company=company,
        role="admin",
    )


@pytest.fixture
def employee_user(company, db):
    from accounts.models import User
    return User.objects.create_user(
        username="employee_user",
        email="employee@test.com",
        password="testpass123",
        first_name="Employee",
        last_name="User",
        company=company,
        role="employee",
    )


@pytest.fixture
def other_admin_user(other_company, db):
    from accounts.models import User
    return User.objects.create_user(
        username="other_admin",
        email="otheradmin@test.com",
        password="testpass123",
        first_name="Other",
        last_name="Admin",
        company=other_company,
        role="admin",
    )


@pytest.fixture
def authenticated_admin(api_client, admin_user, subscription):
    """APIClient authenticated as admin with an active subscription."""
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def authenticated_employee(api_client, employee_user, subscription):
    """APIClient authenticated as employee with an active subscription."""
    api_client.force_authenticate(user=employee_user)
    return api_client
