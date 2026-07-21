"""Tests for per-user can_delete_clients permission on customer delete."""
import pytest
from rest_framework import status

from conftest import api_body


@pytest.mark.django_db
class TestCanDeleteClients:
    def test_admin_can_delete_without_flag(self, authenticated_admin, company):
        from crm.models import Client

        client = Client.objects.create(
            name="AdminDelete", company=company, priority="low", type="cold"
        )
        response = authenticated_admin.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Client.objects.filter(id=client.id).exists()

    def test_employee_without_flag_cannot_delete(
        self, authenticated_employee, employee_user, company
    ):
        from crm.models import Client

        client = Client.objects.create(
            name="NoDelete",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        assert employee_user.can_delete_clients is False
        response = authenticated_employee.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        body = api_body(response)
        code = None
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                code = err.get("code")
            else:
                code = body.get("code")
        assert code == "cannot_delete_clients"
        assert Client.objects.filter(id=client.id).exists()

    def test_employee_with_flag_can_delete_assigned(
        self, authenticated_employee, employee_user, company
    ):
        from crm.models import Client

        employee_user.can_delete_clients = True
        employee_user.save(update_fields=["can_delete_clients"])
        client = Client.objects.create(
            name="WithFlag",
            company=company,
            priority="low",
            type="cold",
            assigned_to=employee_user,
        )
        response = authenticated_employee.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Client.objects.filter(id=client.id).exists()

    def test_employee_with_flag_cannot_delete_unassigned(
        self, authenticated_employee, employee_user, company, admin_user
    ):
        from crm.models import Client

        employee_user.can_delete_clients = True
        employee_user.save(update_fields=["can_delete_clients"])
        client = Client.objects.create(
            name="OtherAssignee",
            company=company,
            priority="low",
            type="cold",
            assigned_to=admin_user,
        )
        response = authenticated_employee.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Client.objects.filter(id=client.id).exists()

    def test_supervisor_without_flag_cannot_delete(self, api_client, company, subscription):
        from accounts.models import User, SupervisorPermission
        from crm.models import Client

        supervisor = User.objects.create_user(
            username="sup_no_delete",
            email="sup_no_delete@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
            can_delete_clients=False,
        )
        SupervisorPermission.objects.create(
            user=supervisor, is_active=True, can_manage_leads=True
        )
        client = Client.objects.create(
            name="SupNoDelete", company=company, priority="low", type="cold"
        )
        api_client.force_authenticate(user=supervisor)
        response = api_client.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Client.objects.filter(id=client.id).exists()

    def test_supervisor_with_flag_and_leads_can_delete(
        self, api_client, company, subscription
    ):
        from accounts.models import User, SupervisorPermission
        from crm.models import Client

        supervisor = User.objects.create_user(
            username="sup_can_delete",
            email="sup_can_delete@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
            can_delete_clients=True,
        )
        SupervisorPermission.objects.create(
            user=supervisor, is_active=True, can_manage_leads=True
        )
        client = Client.objects.create(
            name="SupDelete", company=company, priority="low", type="cold"
        )
        api_client.force_authenticate(user=supervisor)
        response = api_client.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Client.objects.filter(id=client.id).exists()

    def test_admin_can_grant_flag_on_employee(
        self, authenticated_admin, employee_user
    ):
        assert employee_user.can_delete_clients is False
        response = authenticated_admin.patch(
            f"/api/v1/users/{employee_user.id}/",
            {"can_delete_clients": True},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["can_delete_clients"] is True
        employee_user.refresh_from_db()
        assert employee_user.can_delete_clients is True

    def test_non_admin_cannot_grant_flag(
        self, authenticated_employee, employee_user, company, subscription
    ):
        from accounts.models import User, SupervisorPermission

        other = User.objects.create_user(
            username="other_emp",
            email="other_emp@test.com",
            password="testpass123",
            company=company,
            role="employee",
        )
        response = authenticated_employee.patch(
            f"/api/v1/users/{other.id}/",
            {"can_delete_clients": True},
            format="json",
        )
        # Employee typically cannot update other users at all; if they can, flag must not stick.
        other.refresh_from_db()
        assert other.can_delete_clients is False

        supervisor = User.objects.create_user(
            username="sup_manage_users",
            email="sup_manage_users@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor, is_active=True, can_manage_users=True
        )
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=supervisor)
        response = client.patch(
            f"/api/v1/users/{employee_user.id}/",
            {"can_delete_clients": True},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_400_BAD_REQUEST,
        )
        employee_user.refresh_from_db()
        assert employee_user.can_delete_clients is False
