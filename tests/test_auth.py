"""
Tests for authentication flows in the CRM API.
"""

import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache

from companies.models import Company
from subscriptions.models import Plan, Subscription, BillingCycle
from accounts.models import OwnerTrustedDevice
from accounts.two_factor_policy import OWNER_TRUST_COOKIE_NAME, hash_device_token, hash_user_agent

User = get_user_model()

@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()


def _with_active_subscription(user, make_owner=True):
    owner = user
    if not make_owner:
        owner = User.objects.create_user(
            username=f"owner_{user.username}",
            email=f"owner_{user.username}@example.com",
            password="securepassword123",
            role="admin",
        )
    company = Company.objects.create(
        name=f"Company {user.username}",
        domain=f"{user.username}.example.com",
        owner=owner,
    )
    if not make_owner:
        owner.company = company
        owner.save(update_fields=["company"])
    user.company = company
    user.save(update_fields=["company"])
    plan = Plan.objects.create(
        name=f"Plan {user.username}",
        description="test",
        price_monthly=10,
        price_yearly=100,
    )
    now = timezone.now()
    Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        start_date=now,
        end_date=now + timedelta(days=30),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
    )
    return company


@pytest.mark.django_db
def test_login_with_username(api_client):
    """Test that a user can log in with their username and password."""
    User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="securepassword123",
        role="employee",
    )
    user = User.objects.get(username="testuser")
    _with_active_subscription(user, make_owner=False)
    url = reverse("token_obtain_pair")
    data = {"username": "testuser", "password": "securepassword123"}
    response = api_client.post(url, data)
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data


@pytest.mark.django_db
def test_login_with_email(api_client):
    """Test that a user can log in using their email address instead of username."""
    User.objects.create_user(
        username="testuser2",
        email="testuser2@example.com",
        password="securepassword123",
        role="employee",
    )
    user = User.objects.get(username="testuser2")
    _with_active_subscription(user, make_owner=False)
    url = reverse("token_obtain_pair")
    data = {"username": "testuser2@example.com", "password": "securepassword123"}
    response = api_client.post(url, data)
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data


@pytest.mark.django_db
def test_login_invalid_password(api_client):
    """Test that login fails gracefully with invalid credentials."""
    User.objects.create_user(
        username="testuser3",
        email="testuser3@example.com",
        password="securepassword123",
        role="employee",
    )
    user = User.objects.get(username="testuser3")
    _with_active_subscription(user)
    url = reverse("token_obtain_pair")
    data = {"username": "testuser3", "password": "wrongpassword"}
    response = api_client.post(url, data)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_non_owner_login_does_not_trigger_2fa(api_client):
    owner = User.objects.create_user(
        username="ownerA",
        email="ownerA@example.com",
        password="securepassword123",
        role="admin",
    )
    company = _with_active_subscription(owner)
    employee = User.objects.create_user(
        username="employeeA",
        email="employeeA@example.com",
        password="securepassword123",
        role="employee",
        company=company,
    )

    url = reverse("token_obtain_pair")
    response = api_client.post(url, {"username": employee.username, "password": "securepassword123"}, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data
    assert response.data.get("requires_two_factor") is None


@pytest.mark.django_db
def test_owner_first_login_requires_2fa(api_client):
    owner = User.objects.create_user(
        username="ownerB",
        email="ownerB@example.com",
        password="securepassword123",
        role="admin",
    )
    _with_active_subscription(owner)

    url = reverse("token_obtain_pair")
    response = api_client.post(url, {"username": owner.username, "password": "securepassword123"}, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert response.data.get("requires_two_factor") is True
    assert "access" not in response.data
    assert response.data.get("token")


@pytest.mark.django_db
def test_owner_trusted_device_skips_2fa(api_client):
    owner = User.objects.create_user(
        username="ownerC",
        email="ownerC@example.com",
        password="securepassword123",
        role="admin",
    )
    _with_active_subscription(owner)

    raw_token = "trusted-device-token"
    OwnerTrustedDevice.objects.create(
        user=owner,
        token_hash=hash_device_token(raw_token),
        user_agent_hash=hash_user_agent("pytest-agent"),
        trusted_until=timezone.now() + timedelta(days=2),
    )
    api_client.cookies[OWNER_TRUST_COOKIE_NAME] = raw_token

    url = reverse("token_obtain_pair")
    response = api_client.post(
        url,
        {"username": owner.username, "password": "securepassword123"},
        format="json",
        HTTP_USER_AGENT="pytest-agent",
    )
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data
    assert response.data.get("requires_two_factor") is None


@pytest.mark.django_db
def test_owner_trusted_device_header_skips_2fa(api_client):
    owner = User.objects.create_user(
        username="ownerHeaderA",
        email="ownerHeaderA@example.com",
        password="securepassword123",
        role="admin",
    )
    _with_active_subscription(owner)

    raw_token = "trusted-device-header-token"
    OwnerTrustedDevice.objects.create(
        user=owner,
        token_hash=hash_device_token(raw_token),
        user_agent_hash=hash_user_agent("pytest-mobile-agent"),
        trusted_until=timezone.now() + timedelta(days=2),
    )

    url = reverse("token_obtain_pair")
    response = api_client.post(
        url,
        {"username": owner.username, "password": "securepassword123"},
        format="json",
        HTTP_USER_AGENT="pytest-mobile-agent",
        HTTP_X_OWNER_TRUSTED_DEVICE=raw_token,
    )
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data
    assert response.data.get("requires_two_factor") is None


@pytest.mark.django_db
def test_owner_expired_trusted_device_requires_2fa(api_client):
    owner = User.objects.create_user(
        username="ownerD",
        email="ownerD@example.com",
        password="securepassword123",
        role="admin",
    )
    _with_active_subscription(owner)

    raw_token = "expired-device-token"
    OwnerTrustedDevice.objects.create(
        user=owner,
        token_hash=hash_device_token(raw_token),
        user_agent_hash=hash_user_agent("pytest-agent"),
        trusted_until=timezone.now() - timedelta(minutes=1),
    )
    api_client.cookies[OWNER_TRUST_COOKIE_NAME] = raw_token

    url = reverse("token_obtain_pair")
    response = api_client.post(
        url,
        {"username": owner.username, "password": "securepassword123"},
        format="json",
        HTTP_USER_AGENT="pytest-agent",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data.get("requires_two_factor") is True


@pytest.mark.django_db
def test_presence_heartbeat_requires_authentication(api_client):
    url = reverse("user-presence-heartbeat")
    response = api_client.post(url, {"source": "web"}, format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_presence_heartbeat_updates_user_presence(api_client):
    user = User.objects.create_user(
        username="presence_user",
        email="presence_user@example.com",
        password="securepassword123",
        role="employee",
    )
    _with_active_subscription(user, make_owner=False)
    api_client.force_authenticate(user=user)

    url = reverse("user-presence-heartbeat")
    response = api_client.post(url, {"source": "mobile"}, format="json")
    assert response.status_code == status.HTTP_200_OK

    user.refresh_from_db()
    assert user.last_seen_at is not None
    assert user.last_seen_source == "mobile"


@pytest.mark.django_db
def test_users_list_includes_online_presence(api_client):
    owner = User.objects.create_user(
        username="presence_owner",
        email="presence_owner@example.com",
        password="securepassword123",
        role="admin",
    )
    company = _with_active_subscription(owner)
    online_employee = User.objects.create_user(
        username="presence_online",
        email="presence_online@example.com",
        password="securepassword123",
        role="employee",
        company=company,
        last_seen_at=timezone.now() - timedelta(seconds=30),
        last_seen_source="web",
    )
    User.objects.create_user(
        username="presence_offline",
        email="presence_offline@example.com",
        password="securepassword123",
        role="data_entry",
        company=company,
        last_seen_at=timezone.now() - timedelta(minutes=10),
        last_seen_source="mobile",
    )

    api_client.force_authenticate(user=owner)
    response = api_client.get(reverse("user-list"))
    assert response.status_code == status.HTTP_200_OK
    payload = response.data.get("data", response.data)
    results = payload.get("results", [])
    target = next(item for item in results if item["id"] == online_employee.id)
    assert target["is_online"] is True
    assert target["last_seen_source"] == "web"
