"""
Phase 6 — convert an inbox conversation into a CRM lead.

This is where a DM finally becomes a lead. The phone-less case is the one to
watch: Instagram carries no phone number, so most converted leads have none, and
nothing may fabricate a placeholder — that would consume the company-wide unique
phone key.
"""

import pytest
from django.utils import timezone

from conftest import api_body

pytestmark = pytest.mark.django_db


def convert_url(pk):
    return f"/api/v1/integrations/inbox/conversations/{pk}/convert/"


@pytest.fixture
def conversation(company, meta_inbox_connection, db):
    from integrations.models import SocialContact, SocialConversation

    contact = SocialContact.objects.create(
        company=company,
        connection=meta_inbox_connection,
        channel="instagram",
        external_id="IGSID-CONV",
        username="hot_lead",
        name="Hot Lead",
    )
    return SocialConversation.objects.create(
        company=company,
        connection=meta_inbox_connection,
        contact=contact,
        channel="instagram",
        last_inbound_at=timezone.now(),
    )


@pytest.fixture
def second_conversation(company, meta_inbox_connection, db):
    from integrations.models import SocialContact, SocialConversation

    contact = SocialContact.objects.create(
        company=company,
        connection=meta_inbox_connection,
        channel="instagram",
        external_id="IGSID-CONV-2",
        username="another",
    )
    return SocialConversation.objects.create(
        company=company,
        connection=meta_inbox_connection,
        contact=contact,
        channel="instagram",
        last_inbound_at=timezone.now(),
    )


class TestConvertHappyPath:
    def test_creates_lead_with_channel_source(
        self, authenticated_call_center, conversation, employee_user, meta_inbox_account
    ):
        from crm.models import Client
        from integrations.models import SocialConversation

        response = authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert response.status_code == 201

        client = Client.objects.get()
        assert client.source == "instagram"
        assert client.name == "Hot Lead"
        assert client.assigned_to_id == employee_user.id
        assert client.assigned_at is not None
        assert client.external_lead_id == "instagram:IGSID-CONV"
        assert client.integration_account_id == meta_inbox_account.id

        row = SocialConversation.objects.get(pk=conversation.pk)
        assert row.client_id == client.id
        assert row.converted_at is not None
        assert row.assigned_to_id == employee_user.id

    def test_messenger_conversation_gets_messenger_source(
        self, authenticated_call_center, company, meta_inbox_connection, employee_user
    ):
        from crm.models import Client
        from integrations.models import SocialContact, SocialConversation

        contact = SocialContact.objects.create(
            company=company,
            connection=meta_inbox_connection,
            channel="messenger",
            external_id="PSID-1",
        )
        conv = SocialConversation.objects.create(
            company=company,
            connection=meta_inbox_connection,
            contact=contact,
            channel="messenger",
        )
        authenticated_call_center.post(
            convert_url(conv.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert Client.objects.get().source == "messenger"

    def test_creates_client_event(self, authenticated_call_center, conversation, employee_user):
        from crm.models import ClientEvent

        authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        event = ClientEvent.objects.get(event_type="created")
        assert event.new_value == "Instagram DM"
        assert "hot_lead" in event.notes

    def test_name_override_is_used(
        self, authenticated_call_center, conversation, employee_user
    ):
        from crm.models import Client

        authenticated_call_center.post(
            convert_url(conversation.id),
            {"name": "Renamed Lead", "assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert Client.objects.get().name == "Renamed Lead"

    def test_auto_assign_uses_the_shared_picker(
        self, authenticated_call_center, conversation, employee_user, monkeypatch
    ):
        from crm.models import Client

        monkeypatch.setattr(
            "crm.assignment.has_assignable_employee", lambda company: True
        )
        monkeypatch.setattr(
            "crm.assignment.get_auto_assign_employee", lambda company: employee_user
        )
        response = authenticated_call_center.post(
            convert_url(conversation.id), {"auto_assign": True}, format="json"
        )
        assert response.status_code == 201
        assert Client.objects.get().assigned_to_id == employee_user.id

    def test_no_assignable_employee_still_converts(
        self, authenticated_call_center, conversation, monkeypatch
    ):
        """An unassigned lead beats losing the agent's triage work."""
        from crm.models import Client

        monkeypatch.setattr(
            "crm.assignment.has_assignable_employee", lambda company: False
        )
        response = authenticated_call_center.post(
            convert_url(conversation.id), {"auto_assign": True}, format="json"
        )
        assert response.status_code == 201
        assert Client.objects.get().assigned_to_id is None


class TestPhonelessLead:
    def test_no_phone_creates_no_phone_row(
        self, authenticated_call_center, conversation, employee_user
    ):
        from crm.models import Client, ClientPhoneNumber

        authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        client = Client.objects.get()
        assert client.phone_number is None
        assert ClientPhoneNumber.objects.count() == 0

    def test_two_phoneless_converts_coexist(
        self, authenticated_call_center, conversation, second_conversation, employee_user
    ):
        """
        The company-wide unique phone key is conditional on a non-empty normalized
        value, so many phone-less leads must coexist. This is why nothing may
        fabricate a placeholder number.
        """
        from crm.models import Client

        first = authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        second = authenticated_call_center.post(
            convert_url(second_conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert Client.objects.count() == 2

    def test_supplied_phone_creates_primary_row(
        self, authenticated_call_center, conversation, employee_user
    ):
        from crm.models import ClientPhoneNumber

        authenticated_call_center.post(
            convert_url(conversation.id),
            {"phone": "+9647701234567", "assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        row = ClientPhoneNumber.objects.get()
        assert row.is_primary is True
        assert row.phone_normalized


class TestDuplicateHandling:
    def test_matching_phone_links_existing_lead(
        self, authenticated_call_center, conversation, company, employee_user
    ):
        """An agent typing a known number must link, not fork a second lead."""
        from crm.models import Client, ClientPhoneNumber
        from integrations.models import SocialConversation

        existing = Client.objects.create(
            company=company, name="Already Known", assigned_to=employee_user
        )
        ClientPhoneNumber.objects.create(
            client=existing, phone_number="+9647701234567", is_primary=True
        )

        response = authenticated_call_center.post(
            convert_url(conversation.id), {"phone": "+9647701234567"}, format="json"
        )
        assert response.status_code == 201
        body = api_body(response)
        assert body["duplicate"] is True
        assert body["client_id"] == existing.id
        assert Client.objects.count() == 1
        assert SocialConversation.objects.get(pk=conversation.pk).client_id == existing.id

    def test_double_convert_returns_409(
        self, authenticated_call_center, conversation, employee_user
    ):
        from crm.models import Client

        authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        second = authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert second.status_code == 409
        assert second.data["error"]["code"] == "social_already_converted"
        assert Client.objects.count() == 1


class TestQuota:
    def test_quota_exceeded_blocks_conversion(
        self, authenticated_call_center, conversation, company, monkeypatch
    ):
        from crm.models import Client
        from integrations.models import SocialConversation
        from rest_framework.exceptions import ValidationError

        def deny(*args, **kwargs):
            raise ValidationError(
                detail={
                    "error": "Lead limit reached for this company plan.",
                    "error_key": "plan_quota_max_clients_exceeded",
                }
            )

        monkeypatch.setattr("integrations.services.social_lead.require_quota", deny)

        response = authenticated_call_center.post(
            convert_url(conversation.id), {"auto_assign": False}, format="json"
        )
        assert response.status_code == 403
        assert response.data["error"]["code"] == "plan_quota_max_clients_exceeded"
        assert Client.objects.count() == 0
        assert SocialConversation.objects.get(pk=conversation.pk).client_id is None


class TestConvertAccessControl:
    def test_call_center_may_convert(
        self, authenticated_call_center, conversation, employee_user
    ):
        response = authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert response.status_code == 201

    def test_owner_may_convert(self, authenticated_admin, conversation, employee_user):
        response = authenticated_admin.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert response.status_code == 201

    def test_employee_may_not_convert(self, authenticated_employee, conversation):
        from crm.models import Client

        response = authenticated_employee.post(
            convert_url(conversation.id), {"auto_assign": True}, format="json"
        )
        assert response.status_code == 403
        assert Client.objects.count() == 0

    def test_cross_tenant_convert_404(
        self, authenticated_call_center, other_company, other_company_inbox_connection, db
    ):
        from crm.models import Client
        from integrations.models import SocialContact, SocialConversation

        contact = SocialContact.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            channel="instagram",
            external_id="OTHER-CONV",
        )
        conv = SocialConversation.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            contact=contact,
            channel="instagram",
        )
        response = authenticated_call_center.post(
            convert_url(conv.id), {"auto_assign": False}, format="json"
        )
        assert response.status_code == 404
        assert Client.objects.count() == 0

    def test_unknown_assignee_rejected(self, authenticated_call_center, conversation):
        from crm.models import Client

        response = authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": 999999, "auto_assign": False},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "social_assignee_not_found"
        assert Client.objects.count() == 0

    def test_assignee_from_another_company_rejected(
        self, authenticated_call_center, conversation, other_admin_user
    ):
        response = authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": other_admin_user.id, "auto_assign": False},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "social_assignee_not_found"


class TestNotifications:
    def test_assignee_notified_and_actor_is_not(
        self, authenticated_call_center, conversation, employee_user, call_center_user
    ):
        from notifications.models import Notification, NotificationType

        authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert Notification.objects.filter(
            user=employee_user, type=NotificationType.LEAD_ASSIGNED
        ).exists()
        # The agent who performed the action does not need telling.
        assert not Notification.objects.filter(
            user=call_center_user, type=NotificationType.LEAD_ASSIGNED
        ).exists()

    def test_integration_log_written(
        self, authenticated_call_center, conversation, employee_user
    ):
        from integrations.models import IntegrationLog

        authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert IntegrationLog.objects.filter(action="meta_inbox_lead_converted").exists()


class TestConvertedConversationVisibility:
    def test_assigned_employee_can_now_see_the_thread(
        self, authenticated_call_center, conversation, employee_user
    ):
        """Conversion is what gives a scoped employee access to the conversation."""
        from rest_framework.test import APIClient

        # A separate client on purpose: authenticated_call_center and
        # authenticated_employee share the one api_client fixture, so requesting
        # both would leave only the last force_authenticate in effect.
        employee_client = APIClient()
        employee_client.force_authenticate(user=employee_user)

        before = employee_client.get("/api/v1/integrations/inbox/conversations/")
        assert api_body(before)["results"] == []

        response = authenticated_call_center.post(
            convert_url(conversation.id),
            {"assigned_to": employee_user.id, "auto_assign": False},
            format="json",
        )
        assert response.status_code == 201

        after = employee_client.get("/api/v1/integrations/inbox/conversations/")
        assert [r["id"] for r in api_body(after)["results"]] == [conversation.id]
