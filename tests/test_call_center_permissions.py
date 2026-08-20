"""
Permission-boundary tests for the CALL_CENTER role (Phase 0: role skeleton only).

CALL_CENTER may search/read every lead in its own company and create new leads,
but may not edit or delete leads, and has no access to deals/tasks/campaigns,
WhatsApp chats/calls, or cross-tenant data.
"""
import pytest
from rest_framework import status

from conftest import api_body


@pytest.mark.django_db
class TestCallCenterLeadAccess:
    def test_list_sees_all_company_leads(self, authenticated_call_center, company):
        from crm.models import Client

        Client.objects.create(name="A", company=company, priority="low", type="cold")
        Client.objects.create(name="B", company=company, priority="high", type="fresh")

        response = authenticated_call_center.get("/api/v1/clients/")
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["count"] == 2

    def test_can_create_lead(self, authenticated_call_center, company):
        data = {
            "name": "Walk-in Lead",
            "priority": "high",
            "type": "fresh",
            "company": company.id,
        }
        response = authenticated_call_center.post("/api/v1/clients/", data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert api_body(response)["name"] == "Walk-in Lead"

    def test_cannot_update_lead(self, authenticated_call_center, company):
        from crm.models import Client

        client = Client.objects.create(
            name="Old Name", company=company, priority="low", type="cold"
        )
        response = authenticated_call_center.patch(
            f"/api/v1/clients/{client.id}/",
            {"name": "New Name"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_delete_lead(self, authenticated_call_center, company):
        from crm.models import Client

        client = Client.objects.create(
            name="ToDelete", company=company, priority="low", type="cold"
        )
        response = authenticated_call_center.delete(f"/api/v1/clients/{client.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Client.objects.filter(id=client.id).exists()

    def test_cannot_bulk_assign(self, authenticated_call_center, company, employee_user):
        from crm.models import Client

        client = Client.objects.create(
            name="Unassigned", company=company, priority="low", type="cold"
        )
        response = authenticated_call_center.post(
            "/api/v1/clients/bulk_assign/",
            {"client_ids": [client.id], "user_id": employee_user.id},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cross_tenant_lead_not_found(self, authenticated_call_center, other_company):
        from crm.models import Client

        other_client = Client.objects.create(
            name="Other Co Lead", company=other_company, priority="low", type="cold"
        )
        response = authenticated_call_center.get(f"/api/v1/clients/{other_client.id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_can_read_client_events(self, authenticated_call_center, company):
        from crm.models import Client, ClientEvent

        client = Client.objects.create(
            name="Lead", company=company, priority="low", type="cold"
        )
        ClientEvent.objects.create(client=client, event_type="created")
        response = authenticated_call_center.get(f"/api/v1/client-events/?client={client.id}")
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["count"] == 1


@pytest.mark.django_db
class TestCallCenterNonLeadAPIDenied:
    def test_deals_denied(self, authenticated_call_center):
        response = authenticated_call_center.get("/api/v1/deals/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_tasks_denied(self, authenticated_call_center):
        response = authenticated_call_center.get("/api/v1/tasks/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_client_tasks_denied(self, authenticated_call_center):
        response = authenticated_call_center.get("/api/v1/client-tasks/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_client_visits_denied(self, authenticated_call_center):
        response = authenticated_call_center.get("/api/v1/client-visits/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_client_calls_denied(self, authenticated_call_center):
        response = authenticated_call_center.get("/api/v1/client-calls/")
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCallCenterLeadSettingsReadOnly:
    """Lead search/create needs these lists (status, tag and channel pickers on the
    create-lead form), so GET must succeed — but the role never edits settings."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/settings/statuses/",
            "/api/v1/settings/stages/",
            "/api/v1/settings/tags/",
            "/api/v1/settings/call-methods/",
            "/api/v1/settings/channels/",
        ],
    )
    def test_can_read_lead_settings(self, authenticated_call_center, path):
        response = authenticated_call_center.get(path)
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/settings/statuses/",
            "/api/v1/settings/stages/",
            "/api/v1/settings/tags/",
            "/api/v1/settings/call-methods/",
            "/api/v1/settings/channels/",
        ],
    )
    def test_cannot_write_lead_settings(self, authenticated_call_center, company, path):
        response = authenticated_call_center.post(
            path, {"name": "Nope", "company": company.id}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_company_settings_still_denied(self, authenticated_call_center):
        response = authenticated_call_center.get("/api/v1/settings/system/")
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCallCenterWhatsAppDenied:
    def test_whatsapp_chats_denied(self, call_center_user):
        from integrations.whatsapp_access import (
            user_can_access_whatsapp_chats,
            user_can_access_whatsapp_calls,
        )

        assert user_can_access_whatsapp_chats(call_center_user) is False
        assert user_can_access_whatsapp_calls(call_center_user) is False


@pytest.mark.django_db
class TestCallCenterEmployeeDeactivation:
    def test_deactivatable_and_no_lead_reassign_prompt(self):
        from accounts.employee_deactivation import (
            DEACTIVATABLE_EMPLOYEE_ROLES,
            ROLES_WITHOUT_LEAD_ASSIGNMENTS,
            role_offers_lead_reassign_prompt,
        )

        assert "call_center" in DEACTIVATABLE_EMPLOYEE_ROLES
        assert "call_center" in ROLES_WITHOUT_LEAD_ASSIGNMENTS
        assert role_offers_lead_reassign_prompt("call_center") is False


@pytest.mark.django_db
class TestCallCenterTenantChat:
    def test_chat_bucket_is_employee_lane(self, call_center_user):
        from tenant_chat.authorization import chat_role_bucket

        assert chat_role_bucket(call_center_user) == "employee_lane"

    def test_eligible_for_company_chat_listing(self, call_center_user):
        from accounts.models import User
        from tenant_chat.authorization import eligible_company_users_queryset

        qs = eligible_company_users_queryset(User.objects.filter(id=call_center_user.id))
        assert qs.filter(id=call_center_user.id).exists()
