"""WhatsApp Cloud Calling webhook + ACL smoke tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from crm.models import Client, ClientCall, ClientCallSource
from integrations.models import (
    IntegrationAccount,
    MessageTemplate,
    WhatsAppAccount,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallRecordingStatus,
    WhatsAppCallStatus,
)
from integrations.services.whatsapp_calling import (
    find_call_permission_template,
    process_calls_webhook_value,
)

@pytest.fixture
def wa_account(company):
    account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Calling Test",
        status="connected",
    )
    account.set_access_token("test-wa-calling-token")
    account.save(update_fields=["access_token"])
    return WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba1",
        phone_number_id="pid_call_1",
        display_phone_number="+15550001111",
        status="connected",
        calling_enabled=True,
    )


@pytest.mark.django_db
def test_process_calls_webhook_creates_ringing_inbound(company, wa_account):
    value = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "15550001111",
            "phone_number_id": wa_account.phone_number_id,
        },
        "contacts": [{"profile": {"name": "Fatima"}, "wa_id": "15559876543"}],
        "calls": [
            {
                "id": "wacid.test.inbound.1",
                "to": "15550001111",
                "from": "15559876543",
                "event": "connect",
                "direction": "USER_INITIATED",
                "timestamp": "1700000000",
                "session": {"sdp_type": "offer", "sdp": "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n"},
            }
        ],
    }
    calls = process_calls_webhook_value(value)
    assert len(calls) == 1
    call = calls[0]
    assert call.meta_call_id == "wacid.test.inbound.1"
    assert call.direction == WhatsAppCallDirection.INBOUND
    assert call.status == WhatsAppCallStatus.RINGING
    assert call.offer_sdp.startswith("v=0")
    assert call.peer_phone == "15559876543"
    assert call.peer_name == "Fatima"


@pytest.mark.django_db
def test_terminate_missed_inbound_logs_client_call(company, wa_account):
    client = Client.objects.create(company=company, name="Lead A")
    value_connect = {
        "metadata": {"phone_number_id": wa_account.phone_number_id},
        "calls": [
            {
                "id": "wacid.test.missed.1",
                "from": "15551112222",
                "event": "connect",
                "direction": "USER_INITIATED",
                "timestamp": "1700000000",
                "session": {"sdp_type": "offer", "sdp": "v=0"},
            }
        ],
    }
    calls = process_calls_webhook_value(value_connect)
    call = calls[0]
    call.client = client
    call.save(update_fields=["client"])

    value_term = {
        "metadata": {"phone_number_id": wa_account.phone_number_id},
        "calls": [
            {
                "id": "wacid.test.missed.1",
                "event": "terminate",
                "timestamp": "1700000060",
                "duration": 0,
            }
        ],
    }
    process_calls_webhook_value(value_term)
    call.refresh_from_db()
    assert call.status == WhatsAppCallStatus.MISSED
    assert call.client_call_id
    cc = ClientCall.objects.get(pk=call.client_call_id)
    assert cc.source == ClientCallSource.WHATSAPP


@pytest.mark.django_db
def test_calls_list_requires_auth(company, wa_account, admin_user, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])

    client_api = APIClient()
    url = reverse("whatsapp_calls_list")
    assert client_api.get(url).status_code in (401, 403)

    client_api.force_authenticate(user=admin_user)
    WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=wa_account,
        meta_call_id="wacid.list.1",
        direction=WhatsAppCallDirection.INBOUND,
        status=WhatsAppCallStatus.ENDED,
        peer_phone="15550009999",
        recording_status=WhatsAppCallRecordingStatus.NONE,
    )
    res = client_api.get(url)
    assert res.status_code == 200
    body = res.json()
    data = body.get("data") or body
    assert data["count"] >= 1


@pytest.mark.django_db
def test_recording_play_rejects_bad_token(company, wa_account):
    call = WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=wa_account,
        meta_call_id="wacid.rec.1",
        direction=WhatsAppCallDirection.INBOUND,
        status=WhatsAppCallStatus.ENDED,
        peer_phone="15550008888",
        recording_status=WhatsAppCallRecordingStatus.READY,
        recording_storage_key="whatsapp_calls/1/x/y.webm",
    )
    client_api = APIClient()
    url = reverse("whatsapp_call_recording_play", kwargs={"pk": call.id})
    res = client_api.get(url, {"token": "not-a-valid-token"})
    assert res.status_code == 403


def test_parse_meta_template_preserves_call_permission_request():
    from integrations.views.templates_whatsapp import _parse_meta_template_components

    body, _ht, _htext, _footer, buttons = _parse_meta_template_components(
        [
            {"type": "BODY", "text": "May we call you?"},
            {"type": "CALL_PERMISSION_REQUEST"},
        ]
    )
    assert body == "May we call you?"
    assert any(
        isinstance(b, dict) and b.get("type") == "call_permission_request" for b in buttons
    )


@pytest.mark.django_db
def test_find_call_permission_template_prefers_marked_approved(company):
    from integrations.models import MessageTemplate
    from integrations.services.whatsapp_calling import find_call_permission_template

    MessageTemplate.objects.create(
        company=company,
        name="plain_utility",
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content="Hello",
        category=MessageTemplate.CATEGORY_UTILITY,
        language="en",
        buttons=[],
        meta_status="APPROVED",
    )
    cpr = MessageTemplate.objects.create(
        company=company,
        name="request_a_call",
        channel_type=MessageTemplate.CHANNEL_WHATSAPP_API,
        content="Please allow us to call you.",
        category=MessageTemplate.CATEGORY_UTILITY,
        language="en",
        buttons=[{"type": "call_permission_request", "button_text": "Call permission"}],
        meta_status="APPROVED",
    )
    found = find_call_permission_template(company)
    assert found is not None
    assert found.id == cpr.id


@pytest.mark.django_db
def test_permission_request_missing_template_returns_code(
    company, wa_account, admin_user, plan, subscription
):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])

    client_api = APIClient()
    client_api.force_authenticate(user=admin_user)
    url = reverse("whatsapp_call_permission_request")
    res = client_api.post(url, {"to": "15551234567"}, format="json")
    assert res.status_code == 400
    body = res.json()
    err = body.get("error") or {}
    assert err.get("code") == "whatsapp_call_permission_template_missing"

@pytest.mark.django_db
def test_outbound_offer_echo_does_not_mark_answered(company, wa_account):
    """Outbound connect with offer SDP must not look like a customer answer."""
    WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=wa_account,
        meta_call_id="wacid.out.offer.echo",
        direction=WhatsAppCallDirection.OUTBOUND,
        status=WhatsAppCallStatus.RINGING,
        peer_phone="15559876543",
        offer_sdp="v=0\r\noffer\r\n",
        recording_status=WhatsAppCallRecordingStatus.NONE,
    )
    process_calls_webhook_value(
        {
            "metadata": {"phone_number_id": wa_account.phone_number_id},
            "calls": [
                {
                    "id": "wacid.out.offer.echo",
                    "to": "15559876543",
                    "event": "connect",
                    "direction": "BUSINESS_INITIATED",
                    "timestamp": "1700000100",
                    "session": {"sdp_type": "offer", "sdp": "v=0\r\noffer-echo\r\n"},
                }
            ],
        }
    )
    call = WhatsAppCall.objects.get(meta_call_id="wacid.out.offer.echo")
    assert not call.answer_sdp
    assert call.answered_at is None
    assert call.status == WhatsAppCallStatus.RINGING
    assert call.recording_status == WhatsAppCallRecordingStatus.NONE


@pytest.mark.django_db
def test_outbound_answer_sdp_marks_answered_and_pending_recording(company, wa_account):
    WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=wa_account,
        meta_call_id="wacid.out.answered",
        direction=WhatsAppCallDirection.OUTBOUND,
        status=WhatsAppCallStatus.RINGING,
        peer_phone="15559876543",
        offer_sdp="v=0\r\noffer\r\n",
        recording_status=WhatsAppCallRecordingStatus.NONE,
    )
    process_calls_webhook_value(
        {
            "metadata": {"phone_number_id": wa_account.phone_number_id},
            "calls": [
                {
                    "id": "wacid.out.answered",
                    "to": "15559876543",
                    "event": "connect",
                    "direction": "BUSINESS_INITIATED",
                    "timestamp": "1700000200",
                    "session": {"sdp_type": "answer", "sdp": "v=0\r\nanswer\r\n"},
                }
            ],
        }
    )
    call = WhatsAppCall.objects.get(meta_call_id="wacid.out.answered")
    assert call.answer_sdp.startswith("v=0")
    assert call.answered_at is not None
    assert call.status == WhatsAppCallStatus.ANSWERED
    assert call.recording_status == WhatsAppCallRecordingStatus.PENDING


@pytest.mark.django_db
def test_store_recording_rejects_unanswered_call(company, wa_account):
    from integrations.services.whatsapp_calling import store_call_recording

    call = WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=wa_account,
        meta_call_id="wacid.no.answer.rec",
        direction=WhatsAppCallDirection.OUTBOUND,
        status=WhatsAppCallStatus.NO_ANSWER,
        peer_phone="15559870000",
        recording_status=WhatsAppCallRecordingStatus.NONE,
    )
    with pytest.raises(ValueError, match="never answered"):
        store_call_recording(call, file_bytes=b"fake-webm", original_filename="x.webm")
    call.refresh_from_db()
    assert call.recording_status == WhatsAppCallRecordingStatus.NONE
    assert not call.recording_storage_key


@pytest.mark.django_db
def test_terminate_unanswered_clears_pending_recording(
    company, wa_account, admin_user, plan, subscription
):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    call = WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=wa_account,
        meta_call_id="wacid.term.no.answer",
        direction=WhatsAppCallDirection.OUTBOUND,
        status=WhatsAppCallStatus.RINGING,
        peer_phone="15559871111",
        agent=admin_user,
        recording_status=WhatsAppCallRecordingStatus.PENDING,
    )
    client_api = APIClient()
    client_api.force_authenticate(user=admin_user)
    with patch("integrations.views.whatsapp_calling.graph_call_action", return_value={}):
        res = client_api.post(
            reverse("whatsapp_call_terminate", kwargs={"pk": call.id}), {}, format="json"
        )
    assert res.status_code == 200
    call.refresh_from_db()
    assert call.status == WhatsAppCallStatus.NO_ANSWER
    assert call.recording_status == WhatsAppCallRecordingStatus.NONE
    assert call.answered_at is None
