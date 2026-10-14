"""Username-only WhatsApp senders (no phone) are stored and answered in the inbox."""

from unittest.mock import patch

import pytest
from django.utils import timezone

from integrations.models import (
    IntegrationAccount,
    IntegrationPlatform,
    SocialContact,
    SocialConversation,
    SocialMessage,
    WhatsAppAccount,
    WhatsAppPurpose,
    WhatsAppUnroutedMessage,
)
from integrations.services.whatsapp_inbox_send import send_whatsapp_inbox_message
from integrations.whatsapp_webhook import process_whatsapp_message

USER_ID = "US.13491208655302741918"


@pytest.fixture
def whatsapp_inbox_number(company):
    account = IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.WHATSAPP_INBOX,
        name="Inbox WA",
        status="connected",
    )
    row = WhatsAppAccount.objects.create(
        company=company,
        integration_account=account,
        waba_id="waba_inbox_user",
        phone_number_id="inbox_phone_user",
        display_phone_number="+9647701111111",
        status="connected",
        purpose=WhatsAppPurpose.INBOX,
    )
    row.set_access_token("test-token")
    row.save()
    return row


def _message(*, wamid, from_number=None, user_id=USER_ID, body="hi"):
    message = {
        "id": wamid,
        "timestamp": str(int(timezone.now().timestamp())),
        "type": "text",
        "text": {"body": body},
        "from_user_id": user_id,
    }
    if from_number:
        message["from"] = from_number
    return message


@pytest.mark.django_db
def test_username_only_inbox_message_is_stored_and_replied_by_recipient(whatsapp_inbox_number, company):
    process_whatsapp_message(
        _message(wamid="wamid.user.1"),
        whatsapp_inbox_number.phone_number_id,
        contacts=[{"user_id": USER_ID, "profile": {"name": "Nehemiah"}}],
        profile_name="Nehemiah",
    )
    contact = SocialContact.objects.get(company=company, channel="whatsapp")
    assert contact.wa_user_id == USER_ID
    assert contact.external_id == USER_ID
    assert contact.name == "Nehemiah"
    assert SocialMessage.objects.filter(external_message_id="wamid.user.1").exists()
    conversation = SocialConversation.objects.get(contact=contact)

    with patch(
        "integrations.services.whatsapp_inbox_send._graph_json_post",
        return_value=(True, {"messages": [{"id": "wamid.out.1"}]}),
    ) as posted:
        result = send_whatsapp_inbox_message(conversation, text="reply")

    assert result["ok"] is True
    payload = posted.call_args[0][2]
    assert payload["recipient"] == USER_ID
    assert "to" not in payload


@pytest.mark.django_db
def test_later_message_with_phone_stays_on_the_same_contact(whatsapp_inbox_number, company):
    pid = whatsapp_inbox_number.phone_number_id
    process_whatsapp_message(_message(wamid="wamid.user.2"), pid)
    process_whatsapp_message(
        _message(wamid="wamid.user.3", from_number="964770000099", body="now with a phone"),
        pid,
    )
    assert SocialContact.objects.filter(company=company, channel="whatsapp").count() == 1
    contact = SocialContact.objects.get(company=company, channel="whatsapp")
    assert contact.wa_user_id == USER_ID
    assert contact.external_id == "964770000099"
    assert SocialMessage.objects.filter(conversation__contact=contact).count() == 2


@pytest.mark.django_db
def test_crm_username_only_message_is_held(company):
    row = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-crm-user",
        phone_number_id="crm-user-1",
        status="connected",
        purpose=WhatsAppPurpose.CRM,
    )
    process_whatsapp_message(_message(wamid="wamid.crm.user"), row.phone_number_id)
    held = WhatsAppUnroutedMessage.objects.get(external_message_id="wamid.crm.user")
    assert held.reason == "no_sender_phone"
    from crm.models import Client

    assert Client.objects.filter(company=company).count() == 0
