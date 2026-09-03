"""
Conditional GET on the endpoints clients poll.

Each of these used to run its full queryset on every tick regardless of whether
anything had changed. They now answer a matching If-None-Match with a bare 304.

Three properties matter for every one of them, and the last two are correctness
rather than performance — a token that is too *stable* serves stale data:

1. A repeat request with the token returns 304.
2. A relevant write rotates the token, so the client refetches.
3. A token is not honoured for a different user or different request params.
"""

from __future__ import annotations

import pytest
from rest_framework import status

from conftest import api_body


def _etag(response) -> str:
    assert response.status_code == status.HTTP_200_OK, response.status_code
    etag = response.get("ETag")
    assert etag, "endpoint did not send an ETag"
    return etag


@pytest.fixture
def whatsapp_account(company, db):
    from integrations.models import WhatsAppAccount

    return WhatsAppAccount.objects.create(
        company=company, waba_id="waba-cond", phone_number_id="pn-cond"
    )


@pytest.fixture
def wa_client(company, db):
    from crm.models import Client

    return Client.objects.create(
        name="Lead", company=company, priority="low", type="cold"
    )


def _inbound_message(client, body="hi"):
    from integrations.models import LeadWhatsAppMessage

    return LeadWhatsAppMessage.objects.create(
        client=client,
        phone_number="964770000000",
        body=body,
        direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
        is_read=False,
    )


@pytest.mark.django_db
class TestWhatsAppConversationsConditional:
    URL = "/api/v1/integrations/whatsapp/conversations/"

    def test_repeat_request_is_304(self, authenticated_admin, wa_client):
        _inbound_message(wa_client)
        etag = _etag(authenticated_admin.get(self.URL))

        again = authenticated_admin.get(self.URL, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_304_NOT_MODIFIED
        assert again.content == b""

    def test_new_message_rotates_the_token(self, authenticated_admin, wa_client):
        _inbound_message(wa_client)
        etag = _etag(authenticated_admin.get(self.URL))

        _inbound_message(wa_client, body="second")

        again = authenticated_admin.get(self.URL, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_200_OK

    def test_marking_read_rotates_the_token(self, authenticated_admin, wa_client):
        """
        The bulk-update trap.

        mark-read is a queryset .update(), so post_save never fires and the signal
        in sync/signals.py cannot see it. Without the explicit bump in that view
        the unread counts in this list would change while the token did not, and
        the client would be told nothing had happened.
        """
        _inbound_message(wa_client)
        first = authenticated_admin.get(self.URL)
        etag = _etag(first)
        assert api_body(first)[0]["unread_count"] == 1

        marked = authenticated_admin.post(
            "/api/v1/integrations/whatsapp/conversations/mark-read/",
            {"client": wa_client.id},
            format="json",
        )
        assert marked.status_code == status.HTTP_200_OK

        again = authenticated_admin.get(self.URL, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_200_OK
        assert api_body(again)[0]["unread_count"] == 0

    def test_token_not_honoured_for_another_user(
        self, authenticated_admin, wa_client, employee_user, subscription
    ):
        """
        These lists are ACL-filtered, so one user's token must never satisfy
        another's request — otherwise a token surviving a logout could 304 the
        next user into the previous one's view.
        """
        from rest_framework.test import APIClient

        _inbound_message(wa_client)
        etag = _etag(authenticated_admin.get(self.URL))

        other = APIClient()
        other.force_authenticate(user=employee_user)
        response = other.get(self.URL, HTTP_IF_NONE_MATCH=etag)
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestWhatsAppCallsConditional:
    LIST = "/api/v1/integrations/whatsapp/calls/"
    LIVE = "/api/v1/integrations/whatsapp/calls/live/"
    PENDING = "/api/v1/integrations/whatsapp/calls/pending/"

    def _make_call(self, company, whatsapp_account, meta_call_id="c1"):
        from integrations.models import WhatsAppCall

        return WhatsAppCall.objects.create(
            company=company,
            whatsapp_account=whatsapp_account,
            meta_call_id=meta_call_id,
        )

    @pytest.mark.parametrize("url_attr", ["LIST", "LIVE", "PENDING"])
    def test_repeat_request_is_304(self, authenticated_admin, url_attr):
        url = getattr(self, url_attr)
        etag = _etag(authenticated_admin.get(url))

        again = authenticated_admin.get(url, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_304_NOT_MODIFIED

    @pytest.mark.parametrize("url_attr", ["LIST", "LIVE", "PENDING"])
    def test_new_call_rotates_the_token(
        self, authenticated_admin, company, whatsapp_account, url_attr
    ):
        url = getattr(self, url_attr)
        etag = _etag(authenticated_admin.get(url))

        self._make_call(company, whatsapp_account, meta_call_id=f"c-{url_attr}")

        again = authenticated_admin.get(url, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_200_OK

    def test_token_is_per_filter(self, authenticated_admin):
        """
        Paging and filters change the response without changing any counter.

        A token that ignored them would answer a filter switch with a 304, and the
        client would render the previous filter's rows under the new tab.
        """
        etag = _etag(authenticated_admin.get(self.LIST))

        filtered = authenticated_admin.get(
            f"{self.LIST}?status=missed", HTTP_IF_NONE_MATCH=etag
        )
        assert filtered.status_code == status.HTTP_200_OK

    def test_whatsapp_message_does_not_rotate_the_calls_token(
        self, authenticated_admin, wa_client
    ):
        """
        The point of splitting the company counter.

        Before the split every company-visible write moved one counter, so an
        inbound chat message invalidated the calls list too and the Calls page
        refetched for something it does not display.
        """
        etag = _etag(authenticated_admin.get(self.LIST))

        _inbound_message(wa_client)

        again = authenticated_admin.get(self.LIST, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_304_NOT_MODIFIED


@pytest.mark.django_db
class TestTenantChatConversationsConditional:
    URL = "/api/v1/tenant-chat/conversations/"

    def test_repeat_request_is_304(self, authenticated_admin):
        etag = _etag(authenticated_admin.get(self.URL))

        again = authenticated_admin.get(self.URL, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_304_NOT_MODIFIED

    def test_new_message_rotates_the_token(
        self, authenticated_admin, admin_user, employee_user, company
    ):
        from tenant_chat.models import ChatConversation, ChatMessage
        from tenant_chat.serializers import normalize_dm_participants

        low, high = normalize_dm_participants(admin_user, employee_user)
        conversation = ChatConversation.objects.create(
            company=company,
            kind=ChatConversation.Kind.DIRECT,
            participant_low=low,
            participant_high=high,
        )

        etag = _etag(authenticated_admin.get(self.URL))

        ChatMessage.objects.create(
            conversation=conversation, sender=employee_user, body="hello"
        )

        again = authenticated_admin.get(self.URL, HTTP_IF_NONE_MATCH=etag)
        assert again.status_code == status.HTTP_200_OK
