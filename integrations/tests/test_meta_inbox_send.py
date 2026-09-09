"""
Phase 4 — Omni-Channel Inbox send path.

The window rules are the heart of this file. Unlike WhatsApp there is no approved
template to reopen a closed thread, so an out-of-window send must be refused
locally before any Graph call — abusing the HUMAN_AGENT tag risks app-level
enforcement against the whole app.
"""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from conftest import api_body

pytestmark = pytest.mark.django_db

SEND_URL = "/api/v1/integrations/inbox/send/"
SEND_MEDIA_URL = "/api/v1/integrations/inbox/send-media/"
WINDOW_URL = "/api/v1/integrations/inbox/window/"


def attachment_url(pk):
    return f"/api/v1/integrations/inbox/messages/{pk}/attachment/"


@pytest.fixture
def conversation(company, meta_inbox_connection, db):
    """A conversation whose last inbound message arrived just now (window open)."""
    from integrations.models import SocialContact, SocialConversation

    contact = SocialContact.objects.create(
        company=company,
        connection=meta_inbox_connection,
        channel="instagram",
        external_id="IGSID-SEND",
        username="buyer",
    )
    return SocialConversation.objects.create(
        company=company,
        connection=meta_inbox_connection,
        contact=contact,
        channel="instagram",
        last_inbound_at=timezone.now(),
    )


def set_last_inbound(conversation, delta):
    from integrations.models import SocialConversation

    SocialConversation.objects.filter(pk=conversation.pk).update(
        last_inbound_at=timezone.now() - delta
    )
    conversation.refresh_from_db()


@pytest.fixture
def graph_ok(monkeypatch):
    """Capture the Graph request instead of making it."""
    calls = []

    class FakeResponse:
        ok = True

        def json(self):
            return {"message_id": "mid-from-meta", "recipient_id": "IGSID-SEND"}

    def fake_post(url, params=None, json=None, data=None, files=None, timeout=None):
        calls.append({"url": url, "params": params, "json": json, "data": data, "files": files})
        return FakeResponse()

    monkeypatch.setattr(
        "integrations.services.meta_inbox_send.requests.post", fake_post
    )
    return calls


def graph_error(monkeypatch, payload, ok=False):
    class FakeResponse:
        def __init__(self):
            self.ok = ok

        def json(self):
            return payload

    monkeypatch.setattr(
        "integrations.services.meta_inbox_send.requests.post",
        lambda *a, **k: FakeResponse(),
    )


class TestWindowResolution:
    def test_inside_24h_is_response_mode(self, conversation):
        from integrations.services.meta_inbox_send import describe_window

        window = describe_window(conversation)
        assert window["open"] is True
        assert window["mode"] == "response"

    def test_past_24h_without_human_agent_is_closed(self, conversation, settings):
        from integrations.services.meta_inbox_send import describe_window

        settings.META_INBOX_HUMAN_AGENT_TAG_ENABLED = False
        set_last_inbound(conversation, timedelta(hours=30))
        window = describe_window(conversation)
        assert window["open"] is False
        assert window["mode"] == "closed"

    def test_past_24h_with_human_agent_is_open(self, conversation, settings):
        from integrations.services.meta_inbox_send import describe_window

        settings.META_INBOX_HUMAN_AGENT_TAG_ENABLED = True
        set_last_inbound(conversation, timedelta(hours=30))
        window = describe_window(conversation)
        assert window["open"] is True
        assert window["mode"] == "human_agent"

    def test_past_7_days_is_closed_even_with_human_agent(self, conversation, settings):
        from integrations.services.meta_inbox_send import describe_window

        settings.META_INBOX_HUMAN_AGENT_TAG_ENABLED = True
        set_last_inbound(conversation, timedelta(days=8))
        assert describe_window(conversation)["mode"] == "closed"

    def test_no_inbound_ever_is_closed(self, conversation):
        """A business cannot open a conversation cold."""
        from integrations.models import SocialConversation
        from integrations.services.meta_inbox_send import describe_window

        SocialConversation.objects.filter(pk=conversation.pk).update(last_inbound_at=None)
        conversation.refresh_from_db()
        assert describe_window(conversation)["mode"] == "closed"

    def test_window_endpoint_reports_mode(self, authenticated_call_center, conversation):
        body = api_body(
            authenticated_call_center.get(WINDOW_URL, {"conversation": conversation.id})
        )
        assert body["mode"] == "response"
        assert body["open"] is True


class TestSendText:
    def test_send_inside_window_uses_response_type(
        self, authenticated_call_center, conversation, graph_ok
    ):
        from integrations.models import SocialMessage

        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "we do deliver"}, format="json"
        )
        assert response.status_code == 201

        sent = graph_ok[0]["json"]
        assert sent["messaging_type"] == "RESPONSE"
        assert "tag" not in sent
        assert sent["recipient"]["id"] == "IGSID-SEND"

        message = SocialMessage.objects.get(direction="outbound")
        assert message.delivery_status == "sent"
        assert message.external_message_id == "mid-from-meta"

    def test_send_goes_through_the_page_endpoint(
        self, authenticated_call_center, conversation, graph_ok, meta_inbox_connection
    ):
        """Instagram DMs send via the Page, not an Instagram host."""
        authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert f"/{meta_inbox_connection.page_id}/messages" in graph_ok[0]["url"]
        assert "graph.facebook.com" in graph_ok[0]["url"]

    def test_human_agent_tag_applied_when_enabled(
        self, authenticated_call_center, conversation, graph_ok, settings
    ):
        settings.META_INBOX_HUMAN_AGENT_TAG_ENABLED = True
        set_last_inbound(conversation, timedelta(hours=30))

        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "following up"}, format="json"
        )
        assert response.status_code == 201
        assert graph_ok[0]["json"]["messaging_type"] == "MESSAGE_TAG"
        assert graph_ok[0]["json"]["tag"] == "HUMAN_AGENT"

    def test_out_of_window_refused_without_touching_graph(
        self, authenticated_call_center, conversation, graph_ok, settings
    ):
        from integrations.models import SocialMessage

        settings.META_INBOX_HUMAN_AGENT_TAG_ENABLED = False
        set_last_inbound(conversation, timedelta(hours=30))

        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "too late"}, format="json"
        )
        assert response.status_code == 403
        assert response.data["error"]["code"] == "social_outside_window"
        assert graph_ok == []
        # A blocked attempt must not litter the thread.
        assert SocialMessage.objects.count() == 0

    def test_empty_text_rejected(self, authenticated_call_center, conversation):
        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "   "}, format="json"
        )
        assert response.status_code == 400

    def test_unsubscribed_contact_refused(
        self, authenticated_call_center, conversation, graph_ok
    ):
        from integrations.models import SocialConversation

        SocialConversation.objects.filter(pk=conversation.pk).update(is_unsubscribed=True)
        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.status_code == 403
        assert response.data["error"]["code"] == "social_contact_unsubscribed"
        assert graph_ok == []

    def test_client_temp_id_echoed_for_reconciliation(
        self, authenticated_call_center, conversation, graph_ok
    ):
        body = api_body(
            authenticated_call_center.post(
                SEND_URL,
                {"conversation": conversation.id, "text": "hi", "client_temp_id": "tmp-9"},
                format="json",
            )
        )
        assert body["client_temp_id"] == "tmp-9"

    def test_conversation_preview_advances(
        self, authenticated_call_center, conversation, graph_ok
    ):
        from integrations.models import SocialConversation

        authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "on its way"}, format="json"
        )
        row = SocialConversation.objects.get(pk=conversation.pk)
        assert row.last_message_direction == "outbound"
        assert row.last_message_preview == "on its way"


class TestSendErrors:
    def test_permission_subcode_maps_to_error_key(
        self, authenticated_call_center, conversation, monkeypatch
    ):
        from integrations.models import SocialMessage

        graph_error(
            monkeypatch,
            {"error": {"code": 10, "error_subcode": 2534022, "message": "no permission"}},
        )
        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "social_permission_denied"

        message = SocialMessage.objects.get(direction="outbound")
        assert message.delivery_status == "failed"
        assert message.error_key == "social_permission_denied"

    def test_failed_row_returned_for_retry_affordance(
        self, authenticated_call_center, conversation, monkeypatch
    ):
        graph_error(monkeypatch, {"error": {"code": 551, "message": "user unavailable"}})
        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.data["error"]["code"] == "social_user_unavailable"
        assert response.data["error"]["details"]["message"]["delivery_status"] == "failed"

    def test_invalid_token_marks_the_connection(
        self, authenticated_call_center, conversation, monkeypatch, meta_inbox_connection
    ):
        """
        A dead token fails every future send silently, so it must surface on the
        connection rather than only on one bubble.
        """
        from integrations.models import MetaInboxConnection

        graph_error(monkeypatch, {"error": {"code": 190, "message": "token expired"}})
        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.data["error"]["code"] == "social_token_invalid"
        assert MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk).status == "error"

    def test_unknown_code_falls_back(self, authenticated_call_center, conversation, monkeypatch):
        graph_error(monkeypatch, {"error": {"code": 999999, "message": "mystery"}})
        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.data["error"]["code"] == "social_send_failed"

    def test_missing_page_token_is_actionable(
        self, authenticated_call_center, conversation, meta_inbox_connection, graph_ok
    ):
        meta_inbox_connection.set_page_access_token(None)
        meta_inbox_connection.save(update_fields=["page_access_token"])

        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.data["error"]["code"] == "meta_inbox_page_token_missing"
        assert graph_ok == []


class TestSendMedia:
    def test_image_send_uses_multipart(
        self, authenticated_call_center, conversation, graph_ok
    ):
        from integrations.models import SocialMessage

        upload = SimpleUploadedFile("photo.jpg", b"x" * 100, content_type="image/jpeg")
        response = authenticated_call_center.post(
            SEND_MEDIA_URL,
            {"conversation": conversation.id, "file": upload},
            format="multipart",
        )
        assert response.status_code == 201
        # Multipart, so our media is never exposed publicly for Meta to fetch.
        assert graph_ok[0]["files"] is not None
        assert graph_ok[0]["json"] is None

        message = SocialMessage.objects.get(direction="outbound")
        assert message.attachment_kind == "image"
        assert message.delivery_status == "sent"

    def test_oversize_rejected_before_graph(
        self, authenticated_call_center, conversation, graph_ok, settings
    ):
        settings.META_INBOX_MAX_MEDIA_BYTES = 50
        upload = SimpleUploadedFile("big.jpg", b"x" * 5000, content_type="image/jpeg")
        response = authenticated_call_center.post(
            SEND_MEDIA_URL,
            {"conversation": conversation.id, "file": upload},
            format="multipart",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "social_media_too_large"
        assert graph_ok == []

    def test_disallowed_type_rejected(
        self, authenticated_call_center, conversation, graph_ok
    ):
        upload = SimpleUploadedFile("bad.exe", b"MZ" * 10, content_type="application/x-msdownload")
        response = authenticated_call_center.post(
            SEND_MEDIA_URL,
            {"conversation": conversation.id, "file": upload},
            format="multipart",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "social_media_type_not_allowed"
        assert graph_ok == []

    def test_file_required(self, authenticated_call_center, conversation):
        response = authenticated_call_center.post(
            SEND_MEDIA_URL, {"conversation": conversation.id}, format="multipart"
        )
        assert response.status_code == 400


class TestAttachmentServing:
    @pytest.fixture
    def message_with_attachment(self, conversation):
        from django.core.files.base import ContentFile
        from integrations.models import SocialMessage

        message = SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_INBOUND,
            external_message_id="att-1",
            attachment_kind="image",
            attachment_mime="image/jpeg",
        )
        message.attachment.save("shot.jpg", ContentFile(b"bytes"), save=True)
        return message

    def test_authorized_user_gets_the_file(
        self, authenticated_call_center, message_with_attachment
    ):
        response = authenticated_call_center.get(attachment_url(message_with_attachment.id))
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, max-age=3600"

    def test_scoped_employee_gets_404(
        self, authenticated_employee, message_with_attachment
    ):
        """Media inherits the thread's ACL, and denial must not confirm existence."""
        response = authenticated_employee.get(attachment_url(message_with_attachment.id))
        assert response.status_code == 404

    def test_missing_attachment_404(self, authenticated_call_center, conversation):
        from integrations.models import SocialMessage

        message = SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_INBOUND,
            body="text only",
        )
        assert authenticated_call_center.get(attachment_url(message.id)).status_code == 404


class TestSendAccessControl:
    def test_employee_cannot_send_to_another_conversation(
        self, authenticated_employee, conversation, graph_ok
    ):
        response = authenticated_employee.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.status_code == 404
        assert graph_ok == []

    def test_data_entry_denied(self, authenticated_data_entry, conversation, graph_ok):
        response = authenticated_data_entry.post(
            SEND_URL, {"conversation": conversation.id, "text": "hi"}, format="json"
        )
        assert response.status_code == 403
        assert graph_ok == []

    def test_cross_tenant_send_404(
        self, authenticated_call_center, other_company, other_company_inbox_connection, graph_ok, db
    ):
        from integrations.models import SocialContact, SocialConversation

        contact = SocialContact.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            channel="instagram",
            external_id="OTHER",
        )
        conv = SocialConversation.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            contact=contact,
            channel="instagram",
            last_inbound_at=timezone.now(),
        )
        response = authenticated_call_center.post(
            SEND_URL, {"conversation": conv.id, "text": "hi"}, format="json"
        )
        assert response.status_code == 404
        assert graph_ok == []
