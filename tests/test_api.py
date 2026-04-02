"""
API tests: CRUD operations, permissions, pagination, search.
"""
import pytest
from rest_framework import status

from conftest import api_body


@pytest.mark.django_db
class TestClientCRUD:
    """Test basic CRUD operations on the Client endpoint."""

    def test_create_client(self, authenticated_admin, company):
        data = {
            "name": "New Lead",
            "priority": "high",
            "type": "fresh",
            "company": company.id,
        }
        response = authenticated_admin.post("/api/v1/clients/", data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert api_body(response)["name"] == "New Lead"

    def test_list_clients(self, authenticated_admin, company):
        from crm.models import Client

        Client.objects.create(name="A", company=company, priority="low", type="cold")
        Client.objects.create(name="B", company=company, priority="high", type="fresh")

        response = authenticated_admin.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["count"] == 2

    def test_update_client(self, authenticated_admin, company):
        from crm.models import Client

        client = Client.objects.create(
            name="Old Name", company=company, priority="low", type="cold"
        )
        response = authenticated_admin.patch(
            f"/api/v1/clients/{client.id}/",
            {"name": "New Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["name"] == "New Name"

    def test_delete_client(self, authenticated_admin, company):
        from crm.models import Client

        client = Client.objects.create(
            name="ToDelete", company=company, priority="low", type="cold"
        )
        response = authenticated_admin.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Client.objects.filter(id=client.id).exists()


@pytest.mark.django_db
class TestDealCRUD:
    """Test basic CRUD operations on the Deal endpoint."""

    def test_create_deal(self, authenticated_admin, company, admin_user):
        from crm.models import Client

        client = Client.objects.create(
            name="Client A", company=company, priority="low", type="cold"
        )
        data = {
            "client": client.id,
            "company": company.id,
            "employee": admin_user.id,
            "stage": "in_progress",
        }
        response = authenticated_admin.post("/api/v1/deals/", data, format="json")
        assert response.status_code == status.HTTP_201_CREATED

    def test_list_deals(self, authenticated_admin, company, admin_user):
        from crm.models import Client, Deal

        client = Client.objects.create(
            name="C", company=company, priority="low", type="cold"
        )
        Deal.objects.create(
            client=client, company=company, employee=admin_user, stage="in_progress"
        )
        response = authenticated_admin.get("/api/v1/deals/")
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["count"] == 1


@pytest.mark.django_db
class TestPagination:
    """Verify that pagination works correctly."""

    def test_pagination_on_clients(self, authenticated_admin, company):
        from crm.models import Client

        for i in range(25):
            Client.objects.create(
                name=f"Lead {i}", company=company, priority="low", type="cold"
            )

        response = authenticated_admin.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_200_OK
        body = api_body(response)
        assert body["count"] == 25
        assert len(body["results"]) == 20
        assert body["next"] is not None

    def test_second_page(self, authenticated_admin, company):
        from crm.models import Client

        for i in range(25):
            Client.objects.create(
                name=f"Lead {i}", company=company, priority="low", type="cold"
            )

        response = authenticated_admin.get("/api/v1/clients/?page=2")
        assert response.status_code == status.HTTP_200_OK
        assert len(api_body(response)["results"]) == 5


@pytest.mark.django_db
class TestPermissions:
    """Test role-based access control."""

    def test_admin_sees_all_company_clients(
        self, authenticated_admin, company, employee_user
    ):
        from crm.models import Client

        Client.objects.create(
            name="Unassigned",
            company=company,
            priority="low",
            type="cold",
        )
        Client.objects.create(
            name="Assigned",
            company=company,
            assigned_to=employee_user,
            priority="high",
            type="fresh",
        )

        response = authenticated_admin.get("/api/v1/clients/")
        assert api_body(response)["count"] == 2

    def test_employee_sees_only_own_clients(
        self, authenticated_employee, company, employee_user, admin_user
    ):
        from crm.models import Client

        Client.objects.create(
            name="Mine",
            company=company,
            assigned_to=employee_user,
            priority="low",
            type="cold",
        )
        Client.objects.create(
            name="NotMine",
            company=company,
            assigned_to=admin_user,
            priority="high",
            type="fresh",
        )

        response = authenticated_employee.get("/api/v1/clients/")
        body = api_body(response)
        assert body["count"] == 1
        assert body["results"][0]["name"] == "Mine"


@pytest.mark.django_db
class TestSearch:
    """Test search/filter functionality."""

    def test_search_clients_by_name(self, authenticated_admin, company):
        from crm.models import Client

        Client.objects.create(
            name="Alice",
            company=company,
            priority="low",
            type="cold",
        )
        Client.objects.create(
            name="Bob",
            company=company,
            priority="high",
            type="fresh",
        )

        response = authenticated_admin.get("/api/v1/clients/?search=Alice")
        assert response.status_code == status.HTTP_200_OK
        body = api_body(response)
        assert body["count"] == 1
        assert body["results"][0]["name"] == "Alice"
