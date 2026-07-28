"""
Tests for super-admin impersonation handoff and subscription bypass.
"""

import pytest
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework.test import APIRequestFactory

from companies.models import Company
from subscriptions.models import Plan, Subscription, BillingCycle
from accounts.models import ImpersonationSession
from accounts.permissions import HasActiveSubscription, is_impersonating
from conftest import api_body

User = get_user_model()


@pytest.fixture
def super_admin(db):
    return User.objects.create_superuser(
        username="superadmin",
        email="superadmin@test.com",
        password="securepassword123",
    )


@pytest.fixture
def tenant_owner(db):
    owner = User.objects.create_user(
        username="tenant_owner",
        email="tenant_owner@test.com",
        password="securepassword123",
        role="admin",
        email_verified=True,
        phone_verified=True,
    )
    company = Company.objects.create(
        name="Impersonate Co",
        domain="impersonate-co.example.com",
        owner=owner,
    )
    owner.company = company
    owner.save(update_fields=["company"])
    return owner


@pytest.fixture
def expired_subscription(tenant_owner, db):
    plan = Plan.objects.create(
        name="Expired Plan",
        description="test",
        price_monthly=10,
        price_yearly=100,
    )
    now = timezone.now()
    return Subscription.objects.create(
        company=tenant_owner.company,
        plan=plan,
        is_active=False,
        start_date=now - timedelta(days=60),
        end_date=now - timedelta(days=1),
        current_period_start=now - timedelta(days=60),
        billing_cycle=BillingCycle.MONTHLY,
    )


@pytest.mark.django_db
def test_impersonate_requires_superuser(api_client, tenant_owner):
    api_client.force_authenticate(user=tenant_owner)
    response = api_client.post(
        "/api/v1/auth/impersonate/",
        {"company_id": tenant_owner.company_id},
        format="json",
    )
    assert response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED)


@pytest.mark.django_db
def test_impersonate_and_exchange_idempotent(api_client, super_admin, tenant_owner):
    api_client.force_authenticate(user=super_admin)
    start = api_client.post(
        "/api/v1/auth/impersonate/",
        {"company_id": tenant_owner.company_id},
        format="json",
    )
    assert start.status_code == status.HTTP_200_OK
    data = api_body(start)
    code = data["impersonation_code"]
    assert data["access"]
    assert data["impersonation"]["active"] is True
    assert data["impersonated_by"]["id"] == super_admin.id

    access = AccessToken(data["access"])
    assert access.get("impersonation") is True
    assert access.get("impersonator_id") == super_admin.id

    api_client.force_authenticate(user=None)
    first = api_client.get(f"/api/v1/auth/impersonate-exchange/?code={code}")
    assert first.status_code == status.HTTP_200_OK
    first_body = api_body(first)
    assert first_body["access"] == data["access"]

    second = api_client.get(f"/api/v1/auth/impersonate-exchange/?code={code}")
    assert second.status_code == status.HTTP_200_OK
    assert api_body(second)["access"] == data["access"]

    session = ImpersonationSession.objects.get(code=code)
    assert session.used_at is not None


@pytest.mark.django_db
def test_impersonate_exchange_expired_code(api_client, super_admin, tenant_owner):
    api_client.force_authenticate(user=super_admin)
    start = api_client.post(
        "/api/v1/auth/impersonate/",
        {"user_id": tenant_owner.id},
        format="json",
    )
    code = api_body(start)["impersonation_code"]
    ImpersonationSession.objects.filter(code=code).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    api_client.force_authenticate(user=None)
    response = api_client.get(f"/api/v1/auth/impersonate-exchange/?code={code}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_impersonate_cannot_target_superuser(api_client, super_admin):
    other = User.objects.create_superuser(
        username="other_super",
        email="other_super@test.com",
        password="securepassword123",
    )
    api_client.force_authenticate(user=super_admin)
    response = api_client.post(
        "/api/v1/auth/impersonate/",
        {"user_id": other.id},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_has_active_subscription_allows_impersonation_jwt(
    api_client, super_admin, tenant_owner, expired_subscription
):
    api_client.force_authenticate(user=super_admin)
    start = api_client.post(
        "/api/v1/auth/impersonate/",
        {"company_id": tenant_owner.company_id},
        format="json",
    )
    access = api_body(start)["access"]
    token = AccessToken(access)

    factory = APIRequestFactory()
    request = factory.get("/api/v1/clients/")
    request.user = tenant_owner
    request.auth = token

    assert is_impersonating(request) is True
    assert HasActiveSubscription().has_permission(request, None) is True


@pytest.mark.django_db
def test_impersonate_end_audits_and_rejects_normal_session(
    api_client, super_admin, tenant_owner, monkeypatch
):
    logged = []

    def fake_log(**kwargs):
        logged.append(kwargs)

    monkeypatch.setattr("settings.services.log_system_action", fake_log)

    api_client.force_authenticate(user=super_admin)
    start = api_client.post(
        "/api/v1/auth/impersonate/",
        {"company_id": tenant_owner.company_id},
        format="json",
    )
    body = api_body(start)

    # Normal owner session cannot call end as impersonation
    api_client.force_authenticate(user=tenant_owner)
    bad = api_client.post("/api/v1/auth/impersonate-end/", {}, format="json")
    assert bad.status_code == status.HTTP_400_BAD_REQUEST

    # Authenticate with impersonation access token (must clear force_authenticate)
    api_client.force_authenticate(user=None)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
    end = api_client.post(
        "/api/v1/auth/impersonate-end/",
        {"refresh": body["refresh"]},
        format="json",
    )
    assert end.status_code == status.HTTP_200_OK, getattr(end, "data", end.content)
    assert api_body(end).get("ended") is True
    assert any(e.get("action") == "impersonation_end" for e in logged)
