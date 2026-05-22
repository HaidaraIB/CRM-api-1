"""
Tests for employee deactivation with optional lead redistribution.
"""
import pytest
from rest_framework import status

from accounts.models import User
from conftest import api_body
from crm.models import Client


@pytest.fixture
def second_employee(company, db):
    return User.objects.create_user(
        username="employee_two",
        email="employee_two@test.com",
        password="testpass123",
        first_name="Second",
        last_name="Employee",
        company=company,
        role="employee",
        is_active=True,
    )


@pytest.mark.django_db
class TestEmployeeDeactivation:
    def test_deactivate_preview(self, authenticated_admin, employee_user, company):
        Client.objects.create(
            name="Lead A",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_admin.get(
            f"/api/v1/users/{employee_user.id}/deactivate-preview/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["assigned_leads_count"] == 1

    def test_deactivate_without_reassign(
        self, authenticated_admin, employee_user, company
    ):
        lead = Client.objects.create(
            name="Lead Keep",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_admin.post(
            f"/api/v1/users/{employee_user.id}/deactivate/",
            {"reassign_leads": False},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["assigned_lead_count"] == 0
        assert data["leads_remaining_on_user"] == 1

        employee_user.refresh_from_db()
        assert employee_user.is_active is False
        lead.refresh_from_db()
        assert lead.assigned_to_id == employee_user.id

    def test_deactivate_with_reassign(
        self,
        authenticated_admin,
        employee_user,
        second_employee,
        company,
    ):
        lead = Client.objects.create(
            name="Lead Move",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_admin.post(
            f"/api/v1/users/{employee_user.id}/deactivate/",
            {"reassign_leads": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["assigned_lead_count"] == 1
        assert data["leads_remaining_on_user"] == 0

        employee_user.refresh_from_db()
        assert employee_user.is_active is False
        lead.refresh_from_db()
        assert lead.assigned_to_id == second_employee.id

    def test_cannot_deactivate_self(self, authenticated_admin, admin_user):
        response = authenticated_admin.post(
            f"/api/v1/users/{admin_user.id}/deactivate/",
            {"reassign_leads": False},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_deactivate_preview_data_entry_no_reassign_options(
        self, authenticated_admin, company, db
    ):
        from accounts.models import User

        de_user = User.objects.create_user(
            username="de_preview",
            email="de_preview@test.com",
            password="testpass123",
            company=company,
            role="data_entry",
        )
        response = authenticated_admin.get(
            f"/api/v1/users/{de_user.id}/deactivate-preview/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["show_lead_reassign_options"] is False

    def test_deactivate_supervisor_with_reassign(
        self,
        authenticated_admin,
        company,
        employee_user,
        second_employee,
        db,
    ):
        from accounts.models import User, SupervisorPermission

        supervisor = User.objects.create_user(
            username="super_deact",
            email="super_deact@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(user=supervisor, is_active=True)
        lead = Client.objects.create(
            name="Supervisor Lead",
            company=company,
            priority="low",
            type="cold",
            assigned_to=supervisor,
        )
        response = authenticated_admin.post(
            f"/api/v1/users/{supervisor.id}/deactivate/",
            {"reassign_leads": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["assigned_lead_count"] == 1
        supervisor.refresh_from_db()
        assert supervisor.is_active is False
        lead.refresh_from_db()
        assert lead.assigned_to_id in (employee_user.id, second_employee.id)

    def test_cannot_deactivate_owner(
        self, authenticated_admin, owner_user, company
    ):
        response = authenticated_admin.post(
            f"/api/v1/users/{owner_user.id}/deactivate/",
            {"reassign_leads": False},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_employee_cannot_deactivate(
        self, authenticated_employee, employee_user, company, db
    ):
        other = User.objects.create_user(
            username="other_emp",
            email="other_emp@test.com",
            password="testpass123",
            company=company,
            role="employee",
        )
        response = authenticated_employee.post(
            f"/api/v1/users/{other.id}/deactivate/",
            {"reassign_leads": False},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )

    def test_reactivate(
        self, authenticated_admin, employee_user, company
    ):
        employee_user.is_active = False
        employee_user.save(update_fields=["is_active"])

        response = authenticated_admin.post(
            f"/api/v1/users/{employee_user.id}/reactivate/",
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        employee_user.refresh_from_db()
        assert employee_user.is_active is True

    def test_inactive_user_frees_employee_quota(
        self, authenticated_admin, company, plan, subscription, employee_user
    ):
        plan.users = "2"
        plan.limits = {"max_employees": 2}
        plan.save(update_fields=["users", "limits"])

        employee_user.is_active = False
        employee_user.save(update_fields=["is_active"])

        response = authenticated_admin.post(
            "/api/v1/users/",
            {
                "username": "new_hire",
                "email": "new_hire@test.com",
                "password": "testpass123",
                "first_name": "New",
                "last_name": "Hire",
                "phone": "1234567890",
                "role": "employee",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
