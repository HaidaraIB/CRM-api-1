"""
Lead Timeline source for the Omni-Channel Inbox.

`GET /integrations/inbox/lead-messages/?client=` is the social equivalent of
`GET /integrations/whatsapp/messages/?client=`: the clients build the lead
timeline from one endpoint per source, and this is that source.

Two things are worth pinning here beyond the happy path:

1. It reuses the inbox ACL rather than lead-view permission. A scoped employee
   sees their own lead's DMs and nobody else's, and reception/data entry get
   nothing at all even though they can open the lead.
2. Rows carry `conversation` and `channel`. A lead can hold an Instagram DM and
   a Messenger thread at once, and the clients need to keep them apart.
"""

import pytest

from conftest import api_body

pytestmark = pytest.mark.django_db

URL = "/api/v1/integrations/inbox/lead-messages/"


@pytest.fixture
def lead(company, db):
    from crm.models import Client

    return Client.objects.create(company=company, name="Converted Lead")


@pytest.fixture
def make_conversation(company, meta_inbox_connection, db):
    from integrations.models import SocialContact, SocialConversation

    def _make(external_id, channel="instagram", client=None, username=""):
        contact = SocialContact.objects.create(
            company=company,
            connection=meta_inbox_connection,
            channel=channel,
            external_id=external_id,
            username=username,
        )
        return SocialConversation.objects.create(
            company=company,
            connection=meta_inbox_connection,
            contact=contact,
            channel=channel,
            client=client,
        )

    return _make


@pytest.fixture
def make_message(db):
    from integrations.models import SocialMessage

    def _make(conversation, body, direction="inbound", **extra):
        return SocialMessage.objects.create(
            conversation=conversation,
            direction=direction,
            external_message_id=f"m-{conversation.id}-{body[:8]}",
            body=body,
            **extra,
        )

    return _make


class TestLeadMessages:
    def test_returns_messages_for_the_lead(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        conv = make_conversation("IGSID-1", client=lead, username="curious_shopper")
        make_message(conv, "do you deliver?")
        make_message(conv, "yes we do", direction="outbound")

        response = authenticated_call_center.get(URL, {"client": lead.id})
        assert response.status_code == 200
        results = api_body(response)["results"]
        assert {r["body"] for r in results} == {"do you deliver?", "yes we do"}

    def test_newest_first(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        """Same ordering as the WhatsApp endpoint; the clients re-sort anyway."""
        conv = make_conversation("IGSID-1", client=lead)
        first = make_message(conv, "first")
        second = make_message(conv, "second")

        results = api_body(
            authenticated_call_center.get(URL, {"client": lead.id})
        )["results"]
        assert [r["id"] for r in results] == [second.id, first.id]

    def test_row_shape(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        conv = make_conversation("IGSID-1", client=lead, username="curious_shopper")
        make_message(conv, "do you deliver?")

        row = api_body(authenticated_call_center.get(URL, {"client": lead.id}))[
            "results"
        ][0]
        assert row["conversation"] == conv.id
        assert row["channel"] == "instagram"
        assert row["contact_name"] == "curious_shopper"
        assert row["direction"] == "inbound"
        assert row["body"] == "do you deliver?"
        assert row["created_by_username"] is None

    def test_attachment_kind_survives_for_media_only_messages(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        """A media message has an empty body — without this the row renders blank."""
        conv = make_conversation("IGSID-1", client=lead)
        make_message(conv, "", attachment_kind="image")

        row = api_body(authenticated_call_center.get(URL, {"client": lead.id}))[
            "results"
        ][0]
        assert row["body"] == ""
        assert row["attachment_kind"] == "image"

    def test_message_metadata_is_not_exposed(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        """Narrower than the thread serializer — a lead view is not an inbox view."""
        conv = make_conversation("IGSID-1", client=lead)
        make_message(conv, "hi", delivery_status="failed", reaction="love")

        row = api_body(authenticated_call_center.get(URL, {"client": lead.id}))[
            "results"
        ][0]
        for absent in ("delivery_status", "delivery_error", "reaction", "is_echo"):
            assert absent not in row

    def test_two_conversations_on_one_lead_stay_distinguishable(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        """
        An Instagram DM and a Messenger thread can both convert onto one lead.
        Without per-row conversation/channel the clients would render two
        different people as a single conversation.
        """
        ig = make_conversation("IGSID-1", channel="instagram", client=lead)
        msgr = make_conversation("PSID-1", channel="messenger", client=lead)
        make_message(ig, "from instagram")
        make_message(msgr, "from messenger")

        results = api_body(
            authenticated_call_center.get(URL, {"client": lead.id})
        )["results"]
        by_body = {r["body"]: r for r in results}
        assert by_body["from instagram"]["conversation"] == ig.id
        assert by_body["from instagram"]["channel"] == "instagram"
        assert by_body["from messenger"]["conversation"] == msgr.id
        assert by_body["from messenger"]["channel"] == "messenger"

    def test_unconverted_conversation_excluded(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        converted = make_conversation("IGSID-1", client=lead)
        stray = make_conversation("IGSID-2")
        make_message(converted, "mine")
        make_message(stray, "not mine")

        results = api_body(
            authenticated_call_center.get(URL, {"client": lead.id})
        )["results"]
        assert [r["body"] for r in results] == ["mine"]

    def test_another_leads_conversation_excluded(
        self, authenticated_call_center, company, lead, make_conversation, make_message
    ):
        from crm.models import Client

        other_lead = Client.objects.create(company=company, name="Someone Else")
        make_message(make_conversation("IGSID-1", client=lead), "mine")
        make_message(make_conversation("IGSID-2", client=other_lead), "theirs")

        results = api_body(
            authenticated_call_center.get(URL, {"client": lead.id})
        )["results"]
        assert [r["body"] for r in results] == ["mine"]

    def test_lead_with_no_conversations_returns_empty(
        self, authenticated_call_center, lead
    ):
        response = authenticated_call_center.get(URL, {"client": lead.id})
        assert response.status_code == 200
        assert api_body(response)["results"] == []

    def test_limit_is_capped(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        conv = make_conversation("IGSID-1", client=lead)
        for i in range(5):
            make_message(conv, f"m{i}")

        results = api_body(
            authenticated_call_center.get(URL, {"client": lead.id, "limit": 2})
        )["results"]
        assert len(results) == 2

    def test_absurd_limit_falls_back_to_the_cap(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        conv = make_conversation("IGSID-1", client=lead)
        make_message(conv, "hi")
        response = authenticated_call_center.get(
            URL, {"client": lead.id, "limit": 10_000}
        )
        assert response.status_code == 200


class TestLeadMessagesRequestValidation:
    def test_client_is_required(self, authenticated_call_center):
        response = authenticated_call_center.get(URL)
        assert response.status_code == 400
        assert response.data["error"]["code"] == "bad_request"

    def test_client_must_be_an_integer(self, authenticated_call_center):
        response = authenticated_call_center.get(URL, {"client": "abc"})
        assert response.status_code == 400
        assert response.data["error"]["code"] == "bad_request"

    def test_unauthenticated_denied(self, api_client, lead):
        assert api_client.get(URL, {"client": lead.id}).status_code in (401, 403)


class TestLeadMessagesAccessMatrix:
    """
    Same ACL as the rest of the inbox, on purpose: one place decides who may read
    a DM. Reception and data entry can open a lead and still get nothing here,
    which is the existing inbox product rule rather than an oversight.
    """

    def test_owner_sees_them(
        self, authenticated_admin, lead, make_conversation, make_message
    ):
        make_message(make_conversation("IGSID-1", client=lead), "hi")
        response = authenticated_admin.get(URL, {"client": lead.id})
        assert response.status_code == 200
        assert len(api_body(response)["results"]) == 1

    def test_employee_sees_their_own_lead(
        self,
        authenticated_employee,
        employee_user,
        company,
        make_conversation,
        make_message,
        db,
    ):
        from crm.models import Client

        own = Client.objects.create(
            company=company, name="Mine", assigned_to=employee_user
        )
        make_message(make_conversation("IGSID-1", client=own), "hi")

        response = authenticated_employee.get(URL, {"client": own.id})
        assert response.status_code == 200
        assert len(api_body(response)["results"]) == 1

    def test_employee_gets_nothing_for_another_employees_lead(
        self, authenticated_employee, lead, make_conversation, make_message
    ):
        """
        Empty, not 404 — the lead's existence is not the secret here, and an
        empty list leaks nothing about who else is assigned to it.
        """
        make_message(make_conversation("IGSID-1", client=lead), "not yours")
        response = authenticated_employee.get(URL, {"client": lead.id})
        assert response.status_code == 200
        assert api_body(response)["results"] == []

    def test_supervisor_with_permission_sees_them(
        self, api_client, company, subscription, lead, make_conversation, make_message, db
    ):
        from accounts.models import SupervisorPermission, User

        user = User.objects.create_user(
            username="sup_lead_msgs", email="slm@test.com", password="x",
            company=company, role="supervisor",
        )
        SupervisorPermission.objects.create(user=user, can_manage_social_inbox=True)
        api_client.force_authenticate(user=user)
        make_message(make_conversation("IGSID-1", client=lead), "hi")

        response = api_client.get(URL, {"client": lead.id})
        assert response.status_code == 200
        assert len(api_body(response)["results"]) == 1

    def test_supervisor_without_permission_denied(
        self, api_client, company, subscription, lead, db
    ):
        from accounts.models import SupervisorPermission, User

        user = User.objects.create_user(
            username="sup_no_lead_msgs", email="snlm@test.com", password="x",
            company=company, role="supervisor",
        )
        SupervisorPermission.objects.create(user=user, can_manage_social_inbox=False)
        api_client.force_authenticate(user=user)
        assert api_client.get(URL, {"client": lead.id}).status_code == 403

    def test_reception_denied(self, api_client, company, subscription, lead, db):
        from accounts.models import User

        user = User.objects.create_user(
            username="reception_lead_msgs", email="rlm@test.com", password="x",
            company=company, role="reception",
        )
        api_client.force_authenticate(user=user)
        assert api_client.get(URL, {"client": lead.id}).status_code == 403

    def test_data_entry_denied(self, authenticated_data_entry, lead):
        assert authenticated_data_entry.get(URL, {"client": lead.id}).status_code == 403


class TestLeadMessagesCrossTenant:
    def test_other_companys_lead_returns_empty(
        self,
        authenticated_call_center,
        other_company,
        other_company_inbox_connection,
        db,
    ):
        from crm.models import Client
        from integrations.models import (
            SocialContact,
            SocialConversation,
            SocialMessage,
        )

        their_lead = Client.objects.create(company=other_company, name="Theirs")
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
            client=their_lead,
        )
        SocialMessage.objects.create(
            conversation=conv,
            direction=SocialMessage.DIRECTION_INBOUND,
            external_message_id="other-1",
            body="secret",
        )

        response = authenticated_call_center.get(URL, {"client": their_lead.id})
        assert response.status_code == 200
        assert api_body(response)["results"] == []


class TestLeadMessagesFreshness:
    def test_repeat_request_is_304(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        make_message(make_conversation("IGSID-1", client=lead), "hi")
        first = authenticated_call_center.get(URL, {"client": lead.id})
        etag = first["ETag"]

        repeat = authenticated_call_center.get(
            URL, {"client": lead.id}, HTTP_IF_NONE_MATCH=etag
        )
        assert repeat.status_code == 304

    def test_new_message_breaks_the_etag(
        self, authenticated_call_center, lead, make_conversation, make_message
    ):
        conv = make_conversation("IGSID-1", client=lead)
        make_message(conv, "hi")
        etag = authenticated_call_center.get(URL, {"client": lead.id})["ETag"]

        make_message(conv, "and another")
        fresh = authenticated_call_center.get(
            URL, {"client": lead.id}, HTTP_IF_NONE_MATCH=etag
        )
        assert fresh.status_code == 200
        assert len(api_body(fresh)["results"]) == 2
