"""
Phase 3 — Omni-Channel Inbox read API and ACL.

The role matrix is the point of this file. Two invariants matter most:

1. CALL_CENTER gets full company-wide access here while still being denied
   WhatsApp and lead editing (asserted in tests/test_call_center_permissions.py).
2. A scoped employee gets 404 — never 403 — on someone else's conversation, so
   they cannot probe which conversations exist.
"""

import pytest

from conftest import api_body

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/integrations/inbox/conversations/"
MARK_READ_URL = "/api/v1/integrations/inbox/conversations/mark-read/"
STATE_URL = "/api/v1/integrations/inbox/conversations/state/"
UNREAD_URL = "/api/v1/integrations/inbox/unread-count/"


def messages_url(pk):
    return f"/api/v1/integrations/inbox/conversations/{pk}/messages/"


@pytest.fixture
def conversation(company, meta_inbox_connection, db):
    from integrations.models import SocialContact, SocialConversation, SocialMessage

    contact = SocialContact.objects.create(
        company=company,
        connection=meta_inbox_connection,
        channel="instagram",
        external_id="IGSID-1",
        username="curious_shopper",
    )
    conv = SocialConversation.objects.create(
        company=company,
        connection=meta_inbox_connection,
        contact=contact,
        channel="instagram",
        unread_count=2,
        last_message_preview="do you deliver?",
        last_message_direction="inbound",
    )
    SocialMessage.objects.create(
        conversation=conv,
        direction=SocialMessage.DIRECTION_INBOUND,
        external_message_id="m-1",
        body="do you deliver?",
        is_read=False,
    )
    SocialMessage.objects.create(
        conversation=conv,
        direction=SocialMessage.DIRECTION_INBOUND,
        external_message_id="m-2",
        body="hello?",
        is_read=False,
    )
    return conv


@pytest.fixture
def employee_conversation(company, meta_inbox_connection, employee_user, db):
    """A conversation converted into a lead assigned to employee_user."""
    from crm.models import Client
    from integrations.models import SocialContact, SocialConversation

    client = Client.objects.create(
        company=company, name="Converted Lead", assigned_to=employee_user
    )
    contact = SocialContact.objects.create(
        company=company,
        connection=meta_inbox_connection,
        channel="instagram",
        external_id="IGSID-2",
    )
    return SocialConversation.objects.create(
        company=company,
        connection=meta_inbox_connection,
        contact=contact,
        channel="instagram",
        client=client,
    )


class TestInboxAccessMatrix:
    def test_call_center_sees_all_company_conversations(
        self, authenticated_call_center, conversation
    ):
        response = authenticated_call_center.get(LIST_URL)
        assert response.status_code == 200
        assert len(api_body(response)["results"]) == 1

    def test_owner_sees_all(self, authenticated_admin, conversation):
        assert len(api_body(authenticated_admin.get(LIST_URL))["results"]) == 1

    def test_reception_denied(self, api_client, company, subscription, db):
        from accounts.models import User

        user = User.objects.create_user(
            username="reception_user",
            email="reception@test.com",
            password="x",
            company=company,
            role="reception",
        )
        api_client.force_authenticate(user=user)
        assert api_client.get(LIST_URL).status_code == 403

    def test_data_entry_denied(self, authenticated_data_entry):
        assert authenticated_data_entry.get(LIST_URL).status_code == 403

    def test_employee_sees_only_their_converted_leads(
        self, authenticated_employee, conversation, employee_conversation
    ):
        response = authenticated_employee.get(LIST_URL)
        assert response.status_code == 200
        results = api_body(response)["results"]
        assert [r["id"] for r in results] == [employee_conversation.id]

    def test_employee_does_not_see_unconverted_even_if_conversation_assigned(
        self, authenticated_employee, employee_user, conversation
    ):
        """conversation.assigned_to is not the staff ACL; conversion + lead assignee is."""
        from integrations.models import SocialConversation

        SocialConversation.objects.filter(pk=conversation.pk).update(
            assigned_to=employee_user
        )
        response = authenticated_employee.get(LIST_URL)
        assert response.status_code == 200
        assert api_body(response)["results"] == []
        assert authenticated_employee.get(messages_url(conversation.id)).status_code == 404

    def test_employee_loses_access_when_lead_is_reassigned(
        self, authenticated_employee, employee_conversation, admin_user
    ):
        from crm.models import Client

        Client.objects.filter(pk=employee_conversation.client_id).update(
            assigned_to=admin_user
        )
        assert api_body(authenticated_employee.get(LIST_URL))["results"] == []
        response = authenticated_employee.get(messages_url(employee_conversation.id))
        assert response.status_code == 404

    def test_doctor_sees_only_their_converted_leads(
        self, api_client, company, subscription, conversation, db
    ):
        from accounts.models import User
        from crm.models import Client
        from integrations.models import SocialContact, SocialConversation

        doctor = User.objects.create_user(
            username="doc_inbox",
            email="doc_inbox@test.com",
            password="x",
            company=company,
            role="doctor",
        )
        client = Client.objects.create(
            company=company, name="Patient Lead", assigned_to=doctor
        )
        contact = SocialContact.objects.create(
            company=company,
            connection=conversation.connection,
            channel="instagram",
            external_id="IGSID-DOC",
        )
        own = SocialConversation.objects.create(
            company=company,
            connection=conversation.connection,
            contact=contact,
            channel="instagram",
            client=client,
        )
        api_client.force_authenticate(user=doctor)
        response = api_client.get(LIST_URL)
        assert response.status_code == 200
        assert [r["id"] for r in api_body(response)["results"]] == [own.id]

    def test_employee_gets_404_not_403_on_another_conversation(
        self, authenticated_employee, conversation
    ):
        """403 would confirm the conversation exists."""
        response = authenticated_employee.get(messages_url(conversation.id))
        assert response.status_code == 404
        assert response.data["error"]["code"] == "social_conversation_not_found"

    def test_supervisor_without_permission_denied(
        self, api_client, company, subscription, conversation, db
    ):
        from accounts.models import SupervisorPermission, User

        user = User.objects.create_user(
            username="sup1", email="s1@test.com", password="x",
            company=company, role="supervisor",
        )
        SupervisorPermission.objects.create(user=user, can_manage_social_inbox=False)
        api_client.force_authenticate(user=user)
        assert api_client.get(LIST_URL).status_code == 403

    def test_supervisor_with_permission_sees_all(
        self, api_client, company, subscription, conversation, db
    ):
        from accounts.models import SupervisorPermission, User

        user = User.objects.create_user(
            username="sup2", email="s2@test.com", password="x",
            company=company, role="supervisor",
        )
        SupervisorPermission.objects.create(user=user, can_manage_social_inbox=True)
        api_client.force_authenticate(user=user)
        response = api_client.get(LIST_URL)
        assert response.status_code == 200
        assert len(api_body(response)["results"]) == 1

    def test_unauthenticated_denied(self, api_client):
        assert api_client.get(LIST_URL).status_code in (401, 403)


class TestCrossTenantIsolation:
    def test_other_company_conversation_absent_from_list(
        self, authenticated_call_center, other_company, other_company_inbox_connection, db
    ):
        from integrations.models import SocialContact, SocialConversation

        contact = SocialContact.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            channel="instagram",
            external_id="OTHER-IGSID",
        )
        SocialConversation.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            contact=contact,
            channel="instagram",
        )
        assert api_body(authenticated_call_center.get(LIST_URL))["results"] == []

    def test_other_company_messages_404(
        self, authenticated_call_center, other_company, other_company_inbox_connection, db
    ):
        from integrations.models import SocialContact, SocialConversation

        contact = SocialContact.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            channel="instagram",
            external_id="OTHER-IGSID",
        )
        conv = SocialConversation.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            contact=contact,
            channel="instagram",
        )
        assert authenticated_call_center.get(messages_url(conv.id)).status_code == 404


class TestConversationList:
    def test_serialized_shape(self, authenticated_call_center, conversation):
        row = api_body(authenticated_call_center.get(LIST_URL))["results"][0]
        assert row["channel"] == "instagram"
        assert row["unread_count"] == 2
        assert row["contact"]["username"] == "curious_shopper"
        assert row["client"] is None
        assert row["status"] == "open"

    def test_counts_computed_before_status_filter(
        self, authenticated_call_center, conversation
    ):
        """The rail must show every bucket's size while one bucket is selected."""
        body = api_body(authenticated_call_center.get(LIST_URL, {"status": "spam"}))
        assert body["results"] == []
        assert body["status_counts"]["open"] == 1

    def test_channel_filter(self, authenticated_call_center, conversation):
        assert len(api_body(authenticated_call_center.get(LIST_URL, {"channel": "instagram"}))["results"]) == 1
        assert api_body(authenticated_call_center.get(LIST_URL, {"channel": "messenger"}))["results"] == []

    def test_converted_filter(self, authenticated_call_center, conversation, employee_conversation):
        unconverted = api_body(authenticated_call_center.get(LIST_URL, {"converted": "no"}))
        assert [r["id"] for r in unconverted["results"]] == [conversation.id]

        converted = api_body(authenticated_call_center.get(LIST_URL, {"converted": "yes"}))
        assert [r["id"] for r in converted["results"]] == [employee_conversation.id]

    def test_search_matches_contact_and_preview(self, authenticated_call_center, conversation):
        assert len(api_body(authenticated_call_center.get(LIST_URL, {"search": "shopper"}))["results"]) == 1
        assert len(api_body(authenticated_call_center.get(LIST_URL, {"search": "deliver"}))["results"]) == 1
        assert api_body(authenticated_call_center.get(LIST_URL, {"search": "zzz"}))["results"] == []

    def test_agent_filter_forbidden_for_scoped_employee(
        self, authenticated_employee, employee_conversation, employee_user
    ):
        response = authenticated_employee.get(LIST_URL, {"agent": str(employee_user.id)})
        assert response.status_code == 403
        assert response.data["error"]["code"] == "social_agent_filter_forbidden"

    def test_ordering_is_whitelisted(self, authenticated_call_center, conversation):
        """An arbitrary ordering value must not reach the ORM."""
        response = authenticated_call_center.get(LIST_URL, {"ordering": "connection__page_access_token"})
        assert response.status_code == 200

    def test_snooze_sweep_reopens_before_listing(
        self, authenticated_call_center, conversation
    ):
        from django.utils import timezone
        from datetime import timedelta
        from integrations.models import SocialConversation, WhatsAppConversationStatus

        SocialConversation.objects.filter(pk=conversation.pk).update(
            status=WhatsAppConversationStatus.SNOOZED,
            snoozed_until=timezone.now() - timedelta(minutes=5),
        )
        body = api_body(authenticated_call_center.get(LIST_URL))
        assert body["results"][0]["status"] == "open"


class TestMessagesEndpoint:
    def test_returns_thread_oldest_first(self, authenticated_call_center, conversation):
        body = api_body(authenticated_call_center.get(messages_url(conversation.id)))
        assert [m["external_message_id"] for m in body["results"]] == ["m-1", "m-2"]

    def test_includes_conversation(self, authenticated_call_center, conversation):
        body = api_body(authenticated_call_center.get(messages_url(conversation.id)))
        assert body["conversation"]["id"] == conversation.id

    def test_missing_conversation_404(self, authenticated_call_center):
        assert authenticated_call_center.get(messages_url(999999)).status_code == 404


class TestMarkRead:
    def test_marks_inbound_read_and_zeroes_counter(
        self, authenticated_call_center, conversation
    ):
        from integrations.models import SocialConversation, SocialMessage

        response = authenticated_call_center.post(
            MARK_READ_URL, {"conversation": conversation.id}, format="json"
        )
        assert response.status_code == 200
        assert api_body(response)["marked"] == 2

        assert not SocialMessage.objects.filter(is_read=False).exists()
        assert SocialConversation.objects.get(pk=conversation.pk).unread_count == 0

    def test_bulk_update_still_bumps_the_slice(
        self, authenticated_call_center, conversation
    ):
        """
        mark-read is a bulk update() and fires no post_save, so without an
        explicit bump the list would 304 with stale unread counts.
        """
        from django.core.cache import cache
        from sync.version import company_slice_key

        key = company_slice_key("inbox", conversation.company_id)
        before = cache.get(key)
        authenticated_call_center.post(
            MARK_READ_URL, {"conversation": conversation.id}, format="json"
        )
        assert cache.get(key) != before

    def test_requires_conversation(self, authenticated_call_center):
        response = authenticated_call_center.post(MARK_READ_URL, {}, format="json")
        assert response.status_code == 400

    def test_employee_cannot_mark_another_conversation_read(
        self, authenticated_employee, conversation
    ):
        response = authenticated_employee.post(
            MARK_READ_URL, {"conversation": conversation.id}, format="json"
        )
        assert response.status_code == 404


class TestConversationState:
    def test_status_change_records_actor(
        self, authenticated_call_center, conversation, call_center_user
    ):
        from integrations.models import SocialConversation

        response = authenticated_call_center.post(
            STATE_URL, {"conversation": conversation.id, "status": "spam"}, format="json"
        )
        assert response.status_code == 200

        row = SocialConversation.objects.get(pk=conversation.pk)
        assert row.status == "spam"
        assert row.status_changed_by_id == call_center_user.id
        assert row.status_changed_at is not None

    def test_invalid_status_rejected(self, authenticated_call_center, conversation):
        response = authenticated_call_center.post(
            STATE_URL, {"conversation": conversation.id, "status": "nonsense"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "social_invalid_status"

    def test_star_toggle(self, authenticated_call_center, conversation):
        from integrations.models import SocialConversation

        authenticated_call_center.post(
            STATE_URL, {"conversation": conversation.id, "is_starred": True}, format="json"
        )
        assert SocialConversation.objects.get(pk=conversation.pk).is_starred is True

    def test_snooze_sets_status(self, authenticated_call_center, conversation):
        from datetime import timedelta
        from django.utils import timezone
        from integrations.models import SocialConversation

        until = (timezone.now() + timedelta(hours=2)).isoformat()
        authenticated_call_center.post(
            STATE_URL, {"conversation": conversation.id, "snoozed_until": until}, format="json"
        )
        row = SocialConversation.objects.get(pk=conversation.pk)
        assert row.status == "snoozed"
        assert row.snoozed_until is not None

    def test_leaving_snooze_clears_the_timer(self, authenticated_call_center, conversation):
        from datetime import timedelta
        from django.utils import timezone
        from integrations.models import SocialConversation

        SocialConversation.objects.filter(pk=conversation.pk).update(
            status="snoozed", snoozed_until=timezone.now() + timedelta(hours=2)
        )
        authenticated_call_center.post(
            STATE_URL, {"conversation": conversation.id, "status": "open"}, format="json"
        )
        row = SocialConversation.objects.get(pk=conversation.pk)
        assert row.status == "open"
        assert row.snoozed_until is None

    def test_empty_body_rejected(self, authenticated_call_center, conversation):
        response = authenticated_call_center.post(
            STATE_URL, {"conversation": conversation.id}, format="json"
        )
        assert response.status_code == 400


class TestConditionalGet:
    def test_etag_replay_returns_304(self, authenticated_call_center, conversation):
        first = authenticated_call_center.get(LIST_URL)
        etag = first["ETag"]
        second = authenticated_call_center.get(LIST_URL, HTTP_IF_NONE_MATCH=etag)
        assert second.status_code == 304

    def test_new_message_invalidates_the_token(
        self, authenticated_call_center, conversation
    ):
        from integrations.models import SocialMessage

        etag = authenticated_call_center.get(LIST_URL)["ETag"]
        SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_INBOUND,
            external_message_id="m-3",
            body="still there?",
            is_read=False,
        )
        assert authenticated_call_center.get(LIST_URL, HTTP_IF_NONE_MATCH=etag).status_code == 200

    def test_changing_filter_does_not_304(self, authenticated_call_center, conversation):
        """The querystring is part of the variant for exactly this reason."""
        etag = authenticated_call_center.get(LIST_URL)["ETag"]
        response = authenticated_call_center.get(
            LIST_URL, {"status": "spam"}, HTTP_IF_NONE_MATCH=etag
        )
        assert response.status_code == 200


class TestUnreadCount:
    def test_counts_unread_inbound(self, authenticated_call_center, conversation):
        assert api_body(authenticated_call_center.get(UNREAD_URL))["unread"] == 2

    def test_scoped_for_employee(
        self, authenticated_employee, conversation, employee_conversation
    ):
        assert api_body(authenticated_employee.get(UNREAD_URL))["unread"] == 0

    def test_digest_badge_present_and_gated(
        self, authenticated_call_center, call_center_user, conversation, authenticated_data_entry, data_entry_user
    ):
        from sync.counts import social_inbox_unread_for_user

        assert social_inbox_unread_for_user(call_center_user) == 2
        # Data entry has no inbox role, so the badge is absent rather than zero.
        assert social_inbox_unread_for_user(data_entry_user) is None
