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

    def test_admin_create_keeps_signal_based_auto_assign_behavior(
        self, authenticated_admin, company, employee_user
    ):
        from accounts.models import User
        from crm.models import Client

        company.auto_assign_enabled = True
        company.save(update_fields=["auto_assign_enabled"])

        employee_two = User.objects.create_user(
            username="employee_auto_assign_two",
            email="employee_auto_assign_two@test.com",
            password="testpass123",
            company=company,
            role="employee",
            is_active=True,
        )

        Client.objects.create(
            name="Seed assigned",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        company.last_data_entry_assigned_employee = employee_user
        company.save(update_fields=["last_data_entry_assigned_employee"])

        response = authenticated_admin.post(
            "/api/v1/clients/",
            {"name": "Admin Added", "priority": "high", "type": "fresh", "company": company.id},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        created = Client.objects.get(id=api_body(response)["id"])
        company.refresh_from_db()
        assert created.assigned_to_id == employee_two.id
        assert company.last_data_entry_assigned_employee_id == employee_user.id

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
class TestDataEntryClient:
    """Data entry role: list all company leads, create with auto-assign, no detail/edit."""

    def test_data_entry_lists_all_company_clients(
        self, authenticated_data_entry, company, employee_user
    ):
        from crm.models import Client

        Client.objects.create(
            name="A",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        Client.objects.create(
            name="B",
            company=company,
            priority="high",
            type="fresh",
            assigned_to=None,
        )
        response = authenticated_data_entry.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["count"] == 2

    def test_data_entry_retrieve_forbidden(
        self, authenticated_data_entry, company, employee_user
    ):
        from crm.models import Client

        client = Client.objects.create(
            name="X",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_data_entry.get(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_data_entry_patch_forbidden(
        self, authenticated_data_entry, company, employee_user
    ):
        from crm.models import Client

        client = Client.objects.create(
            name="X",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_data_entry.patch(
            f"/api/v1/clients/{client.id}/",
            {"name": "Y"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_data_entry_create_assigns_to_employee(
        self, authenticated_data_entry, company, employee_user
    ):
        data = {
            "name": "Intake Lead",
            "priority": "high",
            "type": "fresh",
            "company": company.id,
        }
        response = authenticated_data_entry.post("/api/v1/clients/", data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        from crm.models import Client

        lead = Client.objects.get(id=api_body(response)["id"])
        assert lead.assigned_to_id == employee_user.id

    def test_data_entry_create_round_robin_sequence_and_wrap(
        self, authenticated_data_entry, company, employee_user
    ):
        from accounts.models import User
        from crm.models import Client

        employee_two = User.objects.create_user(
            username="employee_two",
            email="employee_two@test.com",
            password="testpass123",
            company=company,
            role="employee",
            is_active=True,
        )
        employee_three = User.objects.create_user(
            username="employee_three",
            email="employee_three@test.com",
            password="testpass123",
            company=company,
            role="employee",
            is_active=True,
        )

        created_ids = []
        for idx in range(4):
            response = authenticated_data_entry.post(
                "/api/v1/clients/",
                {
                    "name": f"RR Lead {idx}",
                    "priority": "low",
                    "type": "fresh",
                    "company": company.id,
                },
                format="json",
            )
            assert response.status_code == status.HTTP_201_CREATED
            created_ids.append(api_body(response)["id"])

        assigned_ids = list(
            Client.objects.filter(id__in=created_ids)
            .order_by("id")
            .values_list("assigned_to_id", flat=True)
        )
        assert assigned_ids == [
            employee_user.id,
            employee_two.id,
            employee_three.id,
            employee_user.id,
        ]

    def test_data_entry_round_robin_recovers_when_last_employee_inactive(
        self, authenticated_data_entry, company, employee_user
    ):
        from accounts.models import User
        from crm.models import Client

        employee_two = User.objects.create_user(
            username="employee_rr_inactive",
            email="employee_rr_inactive@test.com",
            password="testpass123",
            company=company,
            role="employee",
            is_active=True,
        )

        company.last_data_entry_assigned_employee = employee_two
        company.save(update_fields=["last_data_entry_assigned_employee"])
        employee_two.is_active = False
        employee_two.save(update_fields=["is_active"])

        response = authenticated_data_entry.post(
            "/api/v1/clients/",
            {
                "name": "Inactive Pointer Recovery",
                "priority": "low",
                "type": "fresh",
                "company": company.id,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        lead = Client.objects.get(id=api_body(response)["id"])
        company.refresh_from_db()
        assert lead.assigned_to_id == employee_user.id
        assert company.last_data_entry_assigned_employee_id == employee_user.id

    def test_data_entry_create_assigns_to_owner_when_no_sales_employee(
        self, authenticated_data_entry, company, employee_user, owner_user
    ):
        from accounts.models import User
        from crm.models import Client

        employee_user.is_active = False
        employee_user.save(update_fields=["is_active"])
        data = {
            "name": "No Emp",
            "priority": "low",
            "type": "cold",
            "company": company.id,
        }
        response = authenticated_data_entry.post("/api/v1/clients/", data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        lead = Client.objects.get(id=api_body(response)["id"])
        company.refresh_from_db()
        assert lead.assigned_to_id == owner_user.id
        assert company.last_data_entry_assigned_employee_id is None

    def test_data_entry_bulk_assign_forbidden(self, authenticated_data_entry, company):
        from crm.models import Client

        c = Client.objects.create(
            name="Z", company=company, priority="low", type="cold"
        )
        response = authenticated_data_entry.post(
            "/api/v1/clients/bulk_assign/",
            {"client_ids": [c.id], "user_id": None},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


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
