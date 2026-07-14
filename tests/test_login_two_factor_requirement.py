"""
Tests for per-owner login 2FA preference.
"""

import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework import status
from django.core.cache import cache

from tests.test_auth import _with_active_subscription

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()


@pytest.mark.django_db
def test_owner_can_update_login_two_factor_enabled(api_client):
    owner = User.objects.create_user(
        username="owner_2fa_pref",
        email="owner_2fa_pref@example.com",
        password="securepassword123",
        role="admin",
    )
    _with_active_subscription(owner)
    api_client.force_authenticate(user=owner)

    url = reverse("user-detail", kwargs={"pk": owner.id})
    r = api_client.patch(url, {"login_two_factor_enabled": False}, format="json")
    assert r.status_code == status.HTTP_200_OK
    owner.refresh_from_db()
    assert owner.login_two_factor_enabled is False


@pytest.mark.django_db
def test_owner_login_skips_2fa_when_disabled(api_client):
    owner = User.objects.create_user(
        username="owner_no_2fa",
        email="owner_no_2fa@example.com",
        password="securepassword123",
        role="admin",
        login_two_factor_enabled=False,
    )
    _with_active_subscription(owner)

    url = reverse("token_obtain_pair")
    response = api_client.post(
        url,
        {"username": owner.username, "password": "securepassword123"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data
    assert response.data.get("requires_two_factor") is None


@pytest.mark.django_db
def test_request_2fa_returns_tokens_when_owner_disabled_2fa(api_client):
    owner = User.objects.create_user(
        username="owner_req_no_2fa",
        email="owner_req_no_2fa@example.com",
        password="securepassword123",
        role="admin",
        login_two_factor_enabled=False,
    )
    _with_active_subscription(owner)

    url = reverse("request_two_factor_auth")
    response = api_client.post(
        url,
        {"username": owner.username, "password": "securepassword123"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["data"]["requires_two_factor"] is False
    assert response.data["data"]["access"]
    assert response.data["data"]["refresh"]


@pytest.mark.django_db
def test_employee_cannot_change_login_two_factor_enabled(api_client):
    owner = User.objects.create_user(
        username="owner_emp_2fa",
        email="owner_emp_2fa@example.com",
        password="securepassword123",
        role="admin",
    )
    company = _with_active_subscription(owner)
    employee = User.objects.create_user(
        username="employee_2fa",
        email="employee_2fa@example.com",
        password="securepassword123",
        role="employee",
        company=company,
        email_verified=True,
        phone_verified=True,
    )
    api_client.force_authenticate(user=employee)

    url = reverse("user-detail", kwargs={"pk": employee.id})
    r = api_client.patch(url, {"login_two_factor_enabled": False}, format="json")
    assert r.status_code == status.HTTP_200_OK
    employee.refresh_from_db()
    assert employee.login_two_factor_enabled is True
