"""Tests for OTPIQ per-company SMS provider."""
from unittest.mock import MagicMock, patch

import pytest

from crm.models import Client
from integrations.models import LeadSMSMessage, SmsProvider, TwilioSettings
from integrations.services.company_sms import send_company_sms
from integrations.services.lead_created_sms import send_lead_created_welcome_sms
from integrations.services.otpiq_sms import (
    normalize_phone_for_otpiq,
    send_custom_message,
    validate_sender_id_for_send,
)


def test_normalize_phone_for_otpiq_strips_plus():
    assert normalize_phone_for_otpiq("+9647701234567") == "9647701234567"
    assert normalize_phone_for_otpiq("07901234567") == "9647901234567"


@patch("integrations.services.otpiq_sms.requests.post")
def test_send_custom_message_success(mock_post):
    mock_post.return_value = MagicMock(
        status_code=200,
        content=b'{"smsId": "sms-abc123"}',
        json=lambda: {"smsId": "sms-abc123"},
    )
    ok, sms_id, err_key, err_msg = send_custom_message(
        api_key="sk_test",
        phone="+9647701234567",
        body="Hello",
        sender_id="BRAND",
    )
    assert ok is True
    assert sms_id == "sms-abc123"
    assert err_key is None
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["json"]["smsType"] == "custom"
    assert call_kwargs["json"]["customMessage"] == "Hello"
    assert call_kwargs["json"]["phoneNumber"] == "9647701234567"
    assert call_kwargs["json"]["senderId"] == "BRAND"
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk_test"


@patch("integrations.services.otpiq_sms.requests.post")
def test_send_custom_message_insufficient_credit(mock_post):
    mock_post.return_value = MagicMock(
        status_code=400,
        content=b'{"error": "Insufficient credit, please add more credit"}',
        json=lambda: {"error": "Insufficient credit, please add more credit"},
    )
    ok, sms_id, err_key, _ = send_custom_message(
        api_key="sk_test",
        phone="9647701234567",
        body="Hi",
    )
    assert ok is False
    assert sms_id is None
    assert err_key == "sms_error_insufficient_credit"


@patch("integrations.services.otpiq_sms.get_sender_id_status")
def test_validate_sender_id_pending(mock_status):
    mock_status.return_value = "pending"
    ok, key, msg = validate_sender_id_for_send("sk_test", "BRAND")
    assert ok is False
    assert key == "sms_error_sender_id_not_approved"
    assert "pending" in (msg or "").lower()


@patch("integrations.services.otpiq_sms.get_sender_id_status")
def test_validate_sender_id_accepted(mock_status):
    mock_status.return_value = "accepted"
    ok, key, msg = validate_sender_id_for_send("sk_test", "BRAND")
    assert ok is True
    assert key is None
    assert msg is None


@pytest.mark.django_db
def test_send_company_sms_blocks_pending_sender_id(company):
    tw = TwilioSettings.objects.create(
        company=company,
        provider=SmsProvider.OTPIQ,
        is_enabled=True,
        sender_id="Shatalarab",
    )
    tw.set_otpiq_api_key("sk_otpiq_test")
    tw.save(update_fields=["otpiq_api_key"])

    with patch("integrations.services.company_sms.otpiq_sms.validate_sender_id_for_send") as mock_validate:
        mock_validate.return_value = (False, "sms_error_sender_id_not_approved", "Sender ID pending")
        with patch("integrations.services.company_sms.otpiq_sms.send_custom_message") as mock_send:
            ok, ext_id, err_key, err_msg, provider = send_company_sms(
                tw,
                to_phone="+9647701112222",
                body="hell world",
            )
    assert ok is False
    assert ext_id is None
    assert err_key == "sms_error_sender_id_not_approved"
    mock_send.assert_not_called()


@pytest.mark.django_db
def test_lead_created_sms_via_otpiq(company):
    tw = TwilioSettings.objects.create(
        company=company,
        provider=SmsProvider.OTPIQ,
        is_enabled=True,
        lead_created_sms_enabled=True,
        lead_created_sms_template="Hello [first_name]",
    )
    tw.set_otpiq_api_key("sk_otpiq_test")
    tw.save(update_fields=["otpiq_api_key"])

    client = Client.objects.create(
        name="Sam Smith",
        company=company,
        priority="low",
        type="fresh",
        phone_number="07901112233",
    )

    with patch("integrations.services.company_sms.otpiq_sms.validate_sender_id_for_send", return_value=(True, None, None)):
        with patch("integrations.services.company_sms.otpiq_sms.send_custom_message") as mock_send:
            mock_send.return_value = (True, "sms-otpiq123", None, None)
            send_lead_created_welcome_sms(client.pk)
            mock_send.assert_called_once()

    rec = LeadSMSMessage.objects.filter(client=client).first()
    assert rec is not None
    assert rec.provider == SmsProvider.OTPIQ
    assert rec.external_message_id == "sms-otpiq123"
    assert rec.twilio_sid is None
    assert "Sam" in rec.body


@pytest.mark.django_db
def test_send_lead_sms_view_otpiq(authenticated_admin, company):
    from conftest import api_body

    tw = TwilioSettings.objects.create(
        company=company,
        provider=SmsProvider.OTPIQ,
        is_enabled=True,
    )
    tw.set_otpiq_api_key("sk_otpiq_test")
    tw.save(update_fields=["otpiq_api_key"])

    client = Client.objects.create(
        name="Lead One",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+9647701112222",
    )

    with patch("integrations.views.twilio_sms.send_company_sms") as mock_send:
        mock_send.return_value = (True, "sms-view123", None, None, SmsProvider.OTPIQ)
        resp = authenticated_admin.post(
            "/api/integrations/twilio/send/",
            {
                "lead_id": client.id,
                "phone_number": "+9647701112222",
                "body": "Test message",
            },
            format="json",
        )

    assert resp.status_code == 201
    data = api_body(resp)
    assert data.get("external_message_id") == "sms-view123"
    assert data.get("body") == "Test message"
    rec = LeadSMSMessage.objects.filter(client=client, external_message_id="sms-view123").first()
    assert rec is not None
    assert rec.body == "Test message"


@pytest.mark.django_db
def test_send_lead_sms_view_stores_exact_body(authenticated_admin, company):
    from conftest import api_body

    tw = TwilioSettings.objects.create(
        company=company,
        provider=SmsProvider.OTPIQ,
        is_enabled=True,
    )
    tw.set_otpiq_api_key("sk_otpiq_test")
    tw.save(update_fields=["otpiq_api_key"])

    client = Client.objects.create(
        name="Lead Two",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+9647703334444",
    )

    with patch("integrations.views.twilio_sms.send_company_sms") as mock_send:
        mock_send.return_value = (True, "sms-body-check", None, None, SmsProvider.OTPIQ)
        resp = authenticated_admin.post(
            "/api/integrations/twilio/send/",
            {
                "lead_id": client.id,
                "phone_number": "+9647703334444",
                "body": "hell world",
            },
            format="json",
        )

    assert resp.status_code == 201
    data = api_body(resp)
    assert data.get("body") == "hell world"
    rec = LeadSMSMessage.objects.get(client=client, external_message_id="sms-body-check")
    assert rec.body == "hell world"
