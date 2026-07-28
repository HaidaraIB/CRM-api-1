"""Tests for WhatsApp coexistence webhook handlers and helpers."""

from unittest.mock import patch

import pytest

from crm.models import Client
from integrations.models import IntegrationAccount, LeadWhatsAppMessage, WhatsAppAccount
from integrations.services.whatsapp_coexistence import (
    FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING,
    extract_whatsapp_message_body,
    is_coexistence_signup_event,
)
from integrations.whatsapp_webhook import (
    process_account_update,
    process_history_sync,
    process_smb_app_state_sync,
    process_smb_message_echoes,
)


@pytest.fixture
def whatsapp_setup(company, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Test",
        status="connected",
        metadata={"coexistence": True},
    )
    account.set_access_token("test-token")
    account.save(update_fields=["access_token"])
    wa = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-100",
        phone_number_id="phone-200",
        display_phone_number="15550783881",
        status="connected",
        integration_account=account,
    )
    wa.set_access_token("test-token")
    wa.save(update_fields=["access_token"])
    return account, wa


@pytest.mark.django_db
def test_is_coexistence_signup_event():
    assert is_coexistence_signup_event(FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING) is True
    assert is_coexistence_signup_event("FINISH") is False
    assert is_coexistence_signup_event(None) is False


def test_extract_whatsapp_message_body_text():
    assert extract_whatsapp_message_body({"type": "text", "text": {"body": "hi"}}) == "hi"
    assert extract_whatsapp_message_body({"type": "image", "image": {}}) == "[image message]"


@pytest.mark.django_db
def test_smb_app_state_sync_creates_client(whatsapp_setup):
    account, wa = whatsapp_setup
    process_smb_app_state_sync(
        {
            "metadata": {
                "display_phone_number": "15550783881",
                "phone_number_id": wa.phone_number_id,
            },
            "state_sync": [
                {
                    "type": "contact",
                    "action": "add",
                    "contact": {
                        "full_name": "Pablo Morales",
                        "first_name": "Pablo",
                        "phone_number": "16505551234",
                    },
                }
            ],
        },
        waba_id=wa.waba_id,
    )
    client = Client.objects.filter(company=account.company, phone_number__icontains="16505551234").first()
    assert client is not None
    assert client.name == "Pablo Morales"


@pytest.mark.django_db
def test_smb_message_echoes_stores_outbound(whatsapp_setup):
    _account, wa = whatsapp_setup
    process_smb_message_echoes(
        {
            "metadata": {
                "display_phone_number": "15550783881",
                "phone_number_id": wa.phone_number_id,
            },
            "message_echoes": [
                {
                    "from": "15550783881",
                    "to": "16505551234",
                    "id": "wamid.echo1",
                    "timestamp": "1700255121",
                    "type": "text",
                    "text": {"body": "Hello from phone"},
                }
            ],
        },
        waba_id=wa.waba_id,
    )
    msg = LeadWhatsAppMessage.objects.get(whatsapp_message_id="wamid.echo1")
    assert msg.direction == LeadWhatsAppMessage.DIRECTION_OUTBOUND
    assert msg.body == "Hello from phone"


@pytest.mark.django_db
def test_history_sync_stores_threads(whatsapp_setup):
    _account, wa = whatsapp_setup
    process_history_sync(
        {
            "messaging_product": "whatsapp",
            "metadata": {
                "display_phone_number": "15550783881",
                "phone_number_id": wa.phone_number_id,
            },
            "history": [
                {
                    "metadata": {"phase": 0, "chunk_order": 1, "progress": 100},
                    "threads": [
                        {
                            "id": "16505551234",
                            "messages": [
                                {
                                    "from": "15550783881",
                                    "id": "wamid.hist.out",
                                    "timestamp": "1739230955",
                                    "type": "text",
                                    "text": {"body": "From business"},
                                    "history_context": {"status": "READ"},
                                },
                                {
                                    "from": "16505551234",
                                    "id": "wamid.hist.in",
                                    "timestamp": "1739231000",
                                    "type": "text",
                                    "text": {"body": "From user"},
                                    "history_context": {"status": "READ"},
                                },
                            ],
                        }
                    ],
                }
            ],
        },
        waba_id=wa.waba_id,
    )
    out_msg = LeadWhatsAppMessage.objects.get(whatsapp_message_id="wamid.hist.out")
    in_msg = LeadWhatsAppMessage.objects.get(whatsapp_message_id="wamid.hist.in")
    assert out_msg.direction == LeadWhatsAppMessage.DIRECTION_OUTBOUND
    assert in_msg.direction == LeadWhatsAppMessage.DIRECTION_INBOUND


@pytest.mark.django_db
def test_history_sync_declined(whatsapp_setup):
    account, wa = whatsapp_setup
    process_history_sync(
        {
            "metadata": {
                "display_phone_number": "15550783881",
                "phone_number_id": wa.phone_number_id,
            },
            "history": [
                {
                    "errors": [
                        {
                            "code": 2593109,
                            "title": "History sync is turned off",
                            "message": "History sync is turned off",
                        }
                    ]
                }
            ],
        },
        waba_id=wa.waba_id,
    )
    account.refresh_from_db()
    assert account.metadata.get("coexistence_history_shared") is False
    assert LeadWhatsAppMessage.objects.count() == 0


@pytest.mark.django_db
def test_partner_removed_disconnects(whatsapp_setup):
    account, wa = whatsapp_setup
    process_account_update(
        {
            "phone_number": "15550783881",
            "event": "PARTNER_REMOVED",
            "disconnection_info": {"reason": "ACCOUNT_DISCONNECTED", "initiated_by": "USER"},
        },
        waba_id=wa.waba_id,
    )
    wa.refresh_from_db()
    account.refresh_from_db()
    assert wa.status == "disconnected"
    assert account.status == "disconnected"
    assert account.metadata.get("coexistence_disconnected") is True


@pytest.mark.django_db
@patch("integrations.services.whatsapp_coexistence.requests.post")
def test_initiate_smb_app_data_sync_calls_both(mock_post, settings):
    from integrations.services.whatsapp_coexistence import initiate_smb_app_data_sync

    class Resp:
        status_code = 200

        def json(self):
            return {"messaging_product": "whatsapp", "request_id": "req-1"}

    mock_post.return_value = Resp()
    result = initiate_smb_app_data_sync("token", "phone-1")
    assert result["contacts"]["request_id"] == "req-1"
    assert result["history"]["request_id"] == "req-1"
    assert result["errors"] == []
    assert mock_post.call_count == 2
    sync_types = [c.kwargs["json"]["sync_type"] for c in mock_post.call_args_list]
    assert sync_types == ["smb_app_state_sync", "history"]
