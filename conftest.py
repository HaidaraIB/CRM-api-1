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


@pytest.fixture(autouse=True)
def _execute_on_commit_in_tests(request):
    """
    pytest-django wraps tests in a transaction that never commits, so
    transaction.on_commit callbacks would never run. Execute them immediately
    unless the test opts into real commit semantics.
    """
    if request.node.get_closest_marker("real_on_commit"):
        yield
        return
    from django.db import transaction as tx

    original = tx.on_commit

    def _immediate(func, using=None, robust=False):
        func()

    tx.on_commit = _immediate
    yield
    tx.on_commit = original


@pytest.fixture(autouse=True)
def _tests_reset_throttle_state():
    """
    DRF stores throttle counters in the cache, which is process-wide and outlives
    a single test. Without this, a test file that makes many anonymous requests
    exhausts the bucket and later tests get 429 instead of their real status.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


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


@pytest.fixture
def data_entry_user(company, db):
    from accounts.models import User

    return User.objects.create_user(
        username="data_entry_user",
        email="dataentry@test.com",
        password="testpass123",
        first_name="Data",
        last_name="Entry",
        company=company,
        role="data_entry",
    )


@pytest.fixture
def authenticated_data_entry(api_client, data_entry_user, subscription):
    """APIClient authenticated as data_entry with an active subscription."""
    api_client.force_authenticate(user=data_entry_user)
    return api_client


@pytest.fixture
def call_center_user(company, db):
    from accounts.models import User

    return User.objects.create_user(
        username="call_center_user",
        email="callcenter@test.com",
        password="testpass123",
        first_name="Call",
        last_name="Center",
        company=company,
        role="call_center",
    )


@pytest.fixture
def authenticated_call_center(api_client, call_center_user, subscription):
    """APIClient authenticated as call_center with an active subscription."""
    api_client.force_authenticate(user=call_center_user)
    return api_client


# --- Omni-Channel Inbox (Instagram DM + Messenger) -------------------------------


@pytest.fixture
def meta_inbox_account(company, owner_user, db):
    """Connected IntegrationAccount for the meta_inbox platform."""
    from integrations.models import IntegrationAccount, IntegrationPlatform

    account = IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.META_INBOX,
        name="Meta Inbox",
        status="connected",
        external_account_id="fbuser-1",
        external_account_name="Test Owner",
        created_by=owner_user,
        metadata={
            "available_pages": [
                {"id": "100000000000001", "name": "Test Page"},
                {"id": "100000000000002", "name": "Second Page"},
            ]
        },
    )
    account.set_access_token("user-token-abc")
    account.save(update_fields=["access_token"])
    return account


@pytest.fixture
def meta_inbox_connection(company, meta_inbox_account, db):
    """A connected Page with a linked Instagram professional account."""
    from integrations.models import MetaInboxConnection

    connection = MetaInboxConnection.objects.create(
        company=company,
        integration_account=meta_inbox_account,
        page_id="100000000000001",
        page_name="Test Page",
        ig_user_id="170000000000001",
        ig_username="testbiz",
        status="connected",
        instagram_subscribed=True,
        messenger_subscribed=True,
        subscribed_fields=["messages", "messaging_postbacks"],
    )
    connection.set_page_access_token("page-token-xyz")
    connection.save(update_fields=["page_access_token"])
    return connection


@pytest.fixture
def other_company_inbox_connection(other_company, db):
    """A connection owned by a different tenant, for cross-tenant isolation tests."""
    from integrations.models import MetaInboxConnection

    connection = MetaInboxConnection.objects.create(
        company=other_company,
        page_id="200000000000001",
        page_name="Other Page",
        ig_user_id="270000000000001",
        ig_username="otherbiz",
        status="connected",
    )
    connection.set_page_access_token("other-page-token")
    connection.save(update_fields=["page_access_token"])
    return connection
