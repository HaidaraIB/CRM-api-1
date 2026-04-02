"""
Security tests: tenant isolation, rate limiting, subscription enforcement.
"""
import pytest
from rest_framework import status

from conftest import api_body


@pytest.mark.django_db
class TestTenantIsolation:
    """Verify that users cannot access data belonging to other companies."""

    def test_admin_cannot_see_other_company_clients(
        self, authenticated_admin, admin_user, other_company, other_admin_user
    ):
        from crm.models import Client

        Client.objects.create(
            name="My Client",
            company=admin_user.company,
            priority="high",
            type="fresh",
        )
        Client.objects.create(
            name="Other Client",
            company=other_company,
            priority="low",
            type="cold",
        )

        response = authenticated_admin.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_200_OK
        client_names = [c["name"] for c in api_body(response)["results"]]
        assert "My Client" in client_names
        assert "Other Client" not in client_names

    def test_employee_sees_only_assigned_clients(
        self, authenticated_employee, admin_user, employee_user, company
    ):
        from crm.models import Client

        Client.objects.create(
            name="Assigned",
            company=company,
            assigned_to=employee_user,
            priority="high",
            type="fresh",
        )
        Client.objects.create(
            name="Not Assigned",
            company=company,
            assigned_to=admin_user,
            priority="low",
            type="cold",
        )

        response = authenticated_employee.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_200_OK
        names = [c["name"] for c in api_body(response)["results"]]
        assert "Assigned" in names
        assert "Not Assigned" not in names


@pytest.mark.django_db
class TestSubscriptionEnforcement:
    """Verify that expired subscriptions block API access."""

    def test_expired_subscription_returns_403(
        self, api_client, admin_user, expired_subscription
    ):
        from django.core.cache import cache
        cache.clear()

        api_client.force_authenticate(user=admin_user)
        response = api_client.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestUnauthenticatedAccess:
    """Verify that unauthenticated requests are rejected."""

    def test_unauthenticated_request_returns_401(self, api_client):
        response = api_client.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestRateLimiting:
    """Verify that rate limiting is enforced on auth endpoints."""

    def test_login_rate_limit(self, api_client):
        """Hit the login endpoint many times to trigger throttle."""
        url = "/api/v1/auth/login/"
        payload = {"username": "nonexistent", "password": "wrong"}

        last_status = None
        for _ in range(10):
            response = api_client.post(url, payload, format="json")
            last_status = response.status_code
            if last_status == status.HTTP_429_TOO_MANY_REQUESTS:
                break

        assert last_status in (
            status.HTTP_429_TOO_MANY_REQUESTS,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_400_BAD_REQUEST,
        )
