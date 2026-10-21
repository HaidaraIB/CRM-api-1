"""Tests for per-user whatsapp_chat_enabled / whatsapp_call_enabled and the
matching SupervisorPermission toggles (can_manage_whatsapp_chats / can_manage_whatsapp_calls).
"""
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from conftest import api_body


def _call_ids(body):
    return {row["meta_call_id"] for row in body["results"]}


def _err_code(response):
    body = api_body(response)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("code")
        return body.get("code")
    return None


@pytest.mark.django_db
class TestWhatsAppChatPermissions:
    def test_employee_chat_disabled_gets_403_on_conversations(
        self, authenticated_employee, employee_user
    ):
        employee_user.whatsapp_chat_enabled = False
        employee_user.save(update_fields=["whatsapp_chat_enabled"])
        response = authenticated_employee.get("/api/v1/integrations/whatsapp/conversations/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(response) == "whatsapp_access_disabled"

    def test_employee_chat_disabled_gets_403_on_send(
        self, authenticated_employee, employee_user
    ):
        employee_user.whatsapp_chat_enabled = False
        employee_user.save(update_fields=["whatsapp_chat_enabled"])
        response = authenticated_employee.post(
            "/api/v1/integrations/whatsapp/send/",
            {"to": "971501234567", "message": "hi"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(response) == "whatsapp_access_disabled"

    def test_employee_chat_enabled_passes_gate_on_conversations(
        self, authenticated_employee, employee_user
    ):
        employee_user.whatsapp_chat_enabled = True
        employee_user.save(update_fields=["whatsapp_chat_enabled"])
        response = authenticated_employee.get("/api/v1/integrations/whatsapp/conversations/")
        # No WhatsApp integration is connected for the test company, so this should not be a 403
        # for whatsapp_access_disabled — either 200 (no conversations) or a different (non-access) error.
        assert _err_code(response) != "whatsapp_access_disabled"

    def test_employee_default_flag_not_blocked(self, authenticated_employee, employee_user):
        # Regression: existing employee rows with the field left at its default (True)
        # must not be blocked by the new gate.
        assert employee_user.whatsapp_chat_enabled is True
        response = authenticated_employee.get("/api/v1/integrations/whatsapp/conversations/")
        assert _err_code(response) != "whatsapp_access_disabled"

    def test_supervisor_without_chat_permission_gets_403(self, company, subscription):
        from accounts.models import User, SupervisorPermission

        supervisor = User.objects.create_user(
            username="sup_no_chat",
            email="sup_no_chat@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor,
            is_active=True,
            can_manage_leads=True,
            can_manage_whatsapp_chats=False,
        )
        client = APIClient()
        client.force_authenticate(user=supervisor)
        response = client.get("/api/v1/integrations/whatsapp/conversations/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(response) == "whatsapp_access_disabled"

    def test_supervisor_with_chat_permission_passes_gate(self, company, subscription):
        from accounts.models import User, SupervisorPermission

        supervisor = User.objects.create_user(
            username="sup_with_chat",
            email="sup_with_chat@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor,
            is_active=True,
            can_manage_leads=True,
            can_manage_whatsapp_chats=True,
        )
        client = APIClient()
        client.force_authenticate(user=supervisor)
        response = client.get("/api/v1/integrations/whatsapp/conversations/")
        assert _err_code(response) != "whatsapp_access_disabled"

    def test_admin_always_passes_gate_regardless_of_flag(
        self, authenticated_admin, admin_user
    ):
        admin_user.whatsapp_chat_enabled = False
        admin_user.save(update_fields=["whatsapp_chat_enabled"])
        response = authenticated_admin.get("/api/v1/integrations/whatsapp/conversations/")
        assert _err_code(response) != "whatsapp_access_disabled"


@pytest.mark.django_db
class TestWhatsAppCallPermissions:
    def test_employee_call_disabled_sees_no_crm_calls(
        self, authenticated_employee, employee_user, company
    ):
        """
        The calls API also serves inbox calls to social-inbox users, so an employee
        with WhatsApp calling switched off still gets 200 — but none of the CRM
        WhatsApp calls, even ones on their own assigned lead.
        """
        from crm.models import Client
        from integrations.models import WhatsAppAccount, WhatsAppCall

        account = WhatsAppAccount.objects.create(company=company, waba_id="w-perm", phone_number_id="pn-perm")
        lead = Client.objects.create(name="L", company=company, priority="low", type="cold", assigned_to=employee_user)
        WhatsAppCall.objects.create(company=company, whatsapp_account=account, meta_call_id="c-perm", client=lead)

        employee_user.whatsapp_call_enabled = True
        employee_user.save(update_fields=["whatsapp_call_enabled"])
        visible = api_body(authenticated_employee.get("/api/v1/integrations/whatsapp/calls/"))
        assert _call_ids(visible) == {"c-perm"}

        employee_user.whatsapp_call_enabled = False
        employee_user.save(update_fields=["whatsapp_call_enabled"])
        response = authenticated_employee.get("/api/v1/integrations/whatsapp/calls/")
        assert response.status_code == status.HTTP_200_OK
        assert _call_ids(api_body(response)) == set()

    def test_employee_call_enabled_passes_gate_on_calls_list(
        self, authenticated_employee, employee_user
    ):
        employee_user.whatsapp_call_enabled = True
        employee_user.save(update_fields=["whatsapp_call_enabled"])
        response = authenticated_employee.get("/api/v1/integrations/whatsapp/calls/")
        assert _err_code(response) != "whatsapp_access_disabled"

    def test_employee_default_call_flag_not_blocked(
        self, authenticated_employee, employee_user
    ):
        assert employee_user.whatsapp_call_enabled is True
        response = authenticated_employee.get(
            "/api/v1/integrations/whatsapp/calls/agent-status/"
        )
        assert _err_code(response) != "whatsapp_access_disabled"

    def test_supervisor_without_call_or_inbox_permission_gets_403(self, company, subscription):
        """Neither WhatsApp calling nor the social inbox: the calls API is closed."""
        from accounts.models import User, SupervisorPermission

        supervisor = User.objects.create_user(
            username="sup_no_call",
            email="sup_no_call@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor,
            is_active=True,
            can_manage_leads=True,
            can_manage_whatsapp_calls=False,
            can_manage_social_inbox=False,
        )
        client = APIClient()
        client.force_authenticate(user=supervisor)
        response = client.get("/api/v1/integrations/whatsapp/calls/agent-status/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(response) == "whatsapp_access_disabled"

    def test_supervisor_with_call_permission_passes_gate(self, company, subscription):
        from accounts.models import User, SupervisorPermission

        supervisor = User.objects.create_user(
            username="sup_with_call",
            email="sup_with_call@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor,
            is_active=True,
            can_manage_leads=True,
            can_manage_whatsapp_calls=True,
        )
        client = APIClient()
        client.force_authenticate(user=supervisor)
        response = client.get("/api/v1/integrations/whatsapp/calls/agent-status/")
        assert _err_code(response) != "whatsapp_access_disabled"

    def test_admin_always_passes_call_gate_regardless_of_flag(
        self, authenticated_admin, admin_user
    ):
        admin_user.whatsapp_call_enabled = False
        admin_user.save(update_fields=["whatsapp_call_enabled"])
        response = authenticated_admin.get("/api/v1/integrations/whatsapp/calls/")
        assert _err_code(response) != "whatsapp_access_disabled"


@pytest.mark.django_db
class TestWhatsAppDeleteLockdown:
    """Only the company owner may delete WhatsApp history (not other admins)."""

    @pytest.fixture
    def wa_client_with_msg(self, company, db):
        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage

        client = Client.objects.create(
            name="Del Lead",
            company=company,
            priority="low",
            type="cold",
            phone_number="9647703333333",
        )
        msg = LeadWhatsAppMessage.objects.create(
            client=client,
            phone_number=client.phone_number,
            body="bye",
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            is_read=True,
        )
        return client, msg

    def test_employee_cannot_delete_conversation(
        self, authenticated_employee, employee_user, wa_client_with_msg
    ):
        client, _msg = wa_client_with_msg
        client.assigned_to = employee_user
        client.save(update_fields=["assigned_to"])
        resp = authenticated_employee.delete(
            f"/api/v1/integrations/whatsapp/conversations/?client={client.id}"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(resp) == "whatsapp_delete_forbidden"

    def test_employee_cannot_delete_message(
        self, authenticated_employee, employee_user, wa_client_with_msg
    ):
        client, msg = wa_client_with_msg
        client.assigned_to = employee_user
        client.save(update_fields=["assigned_to"])
        resp = authenticated_employee.delete(
            f"/api/v1/integrations/whatsapp/messages/{msg.id}/"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(resp) == "whatsapp_delete_forbidden"

    def test_supervisor_with_chats_cannot_delete(
        self, company, subscription, wa_client_with_msg
    ):
        from accounts.models import User, SupervisorPermission

        supervisor = User.objects.create_user(
            username="sup_del",
            email="sup_del@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor,
            is_active=True,
            can_manage_leads=True,
            can_manage_whatsapp_chats=True,
        )
        client, msg = wa_client_with_msg
        api = APIClient()
        api.force_authenticate(user=supervisor)
        resp = api.delete(
            f"/api/v1/integrations/whatsapp/conversations/?client={client.id}"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(resp) == "whatsapp_delete_forbidden"
        resp2 = api.delete(f"/api/v1/integrations/whatsapp/messages/{msg.id}/")
        assert resp2.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(resp2) == "whatsapp_delete_forbidden"

    def test_doctor_cannot_delete(self, company, subscription, wa_client_with_msg):
        from accounts.models import User

        doctor = User.objects.create_user(
            username="doc_del",
            email="doc_del@test.com",
            password="testpass123",
            company=company,
            role="doctor",
        )
        client, msg = wa_client_with_msg
        client.assigned_to = doctor
        client.save(update_fields=["assigned_to"])
        api = APIClient()
        api.force_authenticate(user=doctor)
        resp = api.delete(
            f"/api/v1/integrations/whatsapp/conversations/?client={client.id}"
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(resp) == "whatsapp_delete_forbidden"
        resp2 = api.delete(f"/api/v1/integrations/whatsapp/messages/{msg.id}/")
        assert resp2.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(resp2) == "whatsapp_delete_forbidden"

    def test_non_owner_admin_cannot_delete(self, authenticated_admin, wa_client_with_msg):
        client, msg = wa_client_with_msg
        resp = authenticated_admin.delete(f"/api/v1/integrations/whatsapp/messages/{msg.id}/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(resp) == "whatsapp_delete_forbidden"
        resp = authenticated_admin.delete(f"/api/v1/integrations/whatsapp/conversations/?client={client.id}")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_can_delete_conversation_and_message(
        self, owner_user, subscription, wa_client_with_msg
    ):
        authenticated_owner = APIClient()
        authenticated_owner.force_authenticate(user=owner_user)
        client, msg = wa_client_with_msg
        resp_msg = authenticated_owner.delete(
            f"/api/v1/integrations/whatsapp/messages/{msg.id}/"
        )
        assert resp_msg.status_code == status.HTTP_204_NO_CONTENT

        from integrations.models import LeadWhatsAppMessage

        LeadWhatsAppMessage.objects.create(
            client=client,
            phone_number=client.phone_number,
            body="again",
            direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            is_read=True,
        )
        resp = authenticated_owner.delete(
            f"/api/v1/integrations/whatsapp/conversations/?client={client.id}"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert api_body(resp)["deleted"] >= 1
