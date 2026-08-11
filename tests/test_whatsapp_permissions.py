"""Tests for per-user whatsapp_chat_enabled / whatsapp_call_enabled and the
matching SupervisorPermission toggles (can_manage_whatsapp_chats / can_manage_whatsapp_calls).
"""
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from conftest import api_body


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
    def test_employee_call_disabled_gets_403_on_calls_list(
        self, authenticated_employee, employee_user
    ):
        employee_user.whatsapp_call_enabled = False
        employee_user.save(update_fields=["whatsapp_call_enabled"])
        response = authenticated_employee.get("/api/v1/integrations/whatsapp/calls/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _err_code(response) == "whatsapp_access_disabled"

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

    def test_supervisor_without_call_permission_gets_403(self, company, subscription):
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
