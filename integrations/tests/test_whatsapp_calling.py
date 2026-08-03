"""WhatsApp Cloud Calling webhook + ACL smoke tests."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from crm.models import Client, ClientCall, ClientCallSource
from integrations.models import (
    WhatsAppAccount,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallRecordingStatus,
    WhatsAppCallStatus,
)
from integrations.services.whatsapp_calling import process_calls_webhook_value


@pytest.fixture
def wa_account(company):
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
