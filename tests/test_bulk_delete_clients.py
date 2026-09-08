"""Tests for POST /api/v1/clients/bulk_delete/."""
import pytest
from rest_framework import status

from conftest import api_body


def _error_code(response):
    body = getattr(response, "data", None) or {}
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("code")
        return body.get("code")
    return None


@pytest.mark.django_db
class TestBulkDeleteClients:
    def test_bulk_delete_by_ids(self, authenticated_admin, company):
        from crm.models import Client

        a = Client.objects.create(name="A", company=company, priority="low", type="cold")
        b = Client.objects.create(name="B", company=company, priority="low", type="cold")
        c = Client.objects.create(name="C", company=company, priority="low", type="cold")

        response = authenticated_admin.post(
            "/api/v1/clients/bulk_delete/",
            {"client_ids": [a.id, b.id], "expected_count": 2},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["deleted_count"] == 2
        assert not Client.objects.filter(id__in=[a.id, b.id]).exists()
        assert Client.objects.filter(id=c.id).exists()

    def test_bulk_delete_select_all_with_filters(self, authenticated_admin, company):
        from crm.models import Client

        for i in range(7):
            Client.objects.create(
                name=f"Cold{i}", company=company, priority="low", type="cold"
            )
        for i in range(3):
            Client.objects.create(
                name=f"Fresh{i}", company=company, priority="low", type="fresh"
            )

        response = authenticated_admin.post(
            "/api/v1/clients/bulk_delete/?type=fresh",
            {"select_all": True, "expected_count": 3},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["deleted_count"] == 3
        assert Client.objects.filter(company=company, type="fresh").count() == 0
        assert Client.objects.filter(company=company, type="cold").count() == 7

    def test_bulk_delete_exclude_ids(self, authenticated_admin, company):
        from crm.models import Client

        leads = [
            Client.objects.create(
                name=f"Keep{i}", company=company, priority="low", type="fresh"
            )
            for i in range(4)
        ]
        keep_id = leads[0].id
        response = authenticated_admin.post(
            "/api/v1/clients/bulk_delete/?type=fresh",
            {
                "select_all": True,
                "exclude_ids": [keep_id],
                "expected_count": 3,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["deleted_count"] == 3
        assert Client.objects.filter(id=keep_id).exists()
        assert Client.objects.filter(company=company, type="fresh").count() == 1

    def test_expected_count_mismatch(self, authenticated_admin, company):
        from crm.models import Client

        Client.objects.create(name="One", company=company, priority="low", type="cold")
        response = authenticated_admin.post(
            "/api/v1/clients/bulk_delete/",
            {"select_all": True, "expected_count": 99},
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert _error_code(response) == "bulk_delete_count_mismatch"
        assert Client.objects.filter(company=company).count() == 1

    def test_empty_body(self, authenticated_admin, company):
        response = authenticated_admin.post(
            "/api/v1/clients/bulk_delete/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert _error_code(response) == "missing_field"

    def test_employee_without_flag_forbidden(
        self, authenticated_employee, employee_user, company
    ):
        from crm.models import Client

        lead = Client.objects.create(
            name="NoFlag",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_employee.post(
            "/api/v1/clients/bulk_delete/",
            {"client_ids": [lead.id]},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _error_code(response) == "cannot_delete_clients"
        assert Client.objects.filter(id=lead.id).exists()

    def test_employee_with_flag_still_forbidden(
        self, authenticated_employee, employee_user, company
    ):
        from crm.models import Client

        employee_user.can_delete_clients = True
        employee_user.save(update_fields=["can_delete_clients"])
        lead = Client.objects.create(
            name="Mine",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_employee.post(
            "/api/v1/clients/bulk_delete/",
            {"client_ids": [lead.id], "expected_count": 1},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _error_code(response) == "cannot_delete_clients"
        assert Client.objects.filter(id=lead.id).exists()

    def test_admin_select_all_without_expected_count(self, authenticated_admin, company):
        from crm.models import Client

        Client.objects.create(name="X", company=company, priority="low", type="cold")
        response = authenticated_admin.post(
            "/api/v1/clients/bulk_delete/",
            {"select_all": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["deleted_count"] == 1
