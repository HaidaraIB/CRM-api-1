"""Inbox WhatsApp channel parity endpoints (location, delete, template params)."""

import pytest
from django.utils import timezone

from conftest import api_body

pytestmark = pytest.mark.django_db

DELETE_URL = "/api/v1/integrations/inbox/conversations/{pk}/"
SEND_TEMPLATE_URL = "/api/v1/integrations/inbox/send-template/"


@pytest.fixture
def wa_inbox_conversation(company, db):
    from integrations.models import (
        SocialChannel,
        SocialContact,
        SocialConversation,
        WhatsAppInboxNumber,
    )

    inbox_number = WhatsAppInboxNumber.objects.create(
        company=company,
        waba_id="waba-inbox",
        phone_number_id="inbox-phone-1",
        display_phone_number="+964700000001",
        status="connected",
    )
    inbox_number.set_access_token("test-inbox-token")
    inbox_number.save(update_fields=["access_token"])
    contact = SocialContact.objects.create(
        company=company,
        channel=SocialChannel.WHATSAPP,
        external_id="964700000099",
        wa_inbox_number=inbox_number,
    )
    return SocialConversation.objects.create(
        company=company,
        channel=SocialChannel.WHATSAPP,
        contact=contact,
        wa_inbox_number=inbox_number,
        last_inbound_at=timezone.now(),
    )


def test_non_owner_admin_cannot_delete_inbox_conversation(
    authenticated_admin, wa_inbox_conversation
):
    resp = authenticated_admin.delete(DELETE_URL.format(pk=wa_inbox_conversation.id))
    assert resp.status_code == 403


def test_owner_can_delete_inbox_conversation(api_client, owner_user, subscription, wa_inbox_conversation):
    conv_id = wa_inbox_conversation.id
    api_client.force_authenticate(user=owner_user)
    resp = api_client.delete(DELETE_URL.format(pk=conv_id))
    assert resp.status_code == 200
    assert api_body(resp).get("deleted") is True


def test_send_template_accepts_body_parameters(
    authenticated_call_center, wa_inbox_conversation, monkeypatch
):
    from integrations.models import MessageTemplate

    template = MessageTemplate.objects.create(
        company=wa_inbox_conversation.company,
        name="hello_inbox",
        channel_type="whatsapp_api",
        content="Hi {{1}}",
        meta_status="APPROVED",
    )

    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return True, "wam-1", None, {}

    monkeypatch.setattr(
        "integrations.services.whatsapp_template_send.send_approved_whatsapp_template",
        fake_send,
    )

    resp = authenticated_call_center.post(
        SEND_TEMPLATE_URL,
        {"conversation": wa_inbox_conversation.id, "template_id": template.id, "body_parameters": ["Sam"]},
        format="json",
    )
    assert resp.status_code == 201
    assert captured.get("body_parameters") == ["Sam"]
