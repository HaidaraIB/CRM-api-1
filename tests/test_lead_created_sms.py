"""Tests for automated welcome SMS on new Client (lead) creation."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import ValidationError

from crm.models import Client, ClientPhoneNumber
from integrations.models import LeadSMSMessage, TwilioSettings
from integrations.services.lead_created_sms import (
    render_lead_created_sms_template,
    resolve_client_sms_phone,
    schedule_lead_created_welcome_sms,
    send_lead_created_welcome_sms,
)


@pytest.mark.django_db
def test_render_template_first_name_and_name(company):
    client = Client.objects.create(
        name="Jane Q Public",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+9647701234567",
        source="manual",
    )
    out = render_lead_created_sms_template("Hi [first_name], full=[name]", client)
    assert out == "Hi Jane, full=Jane Q Public"


@pytest.mark.django_db
def test_resolve_phone_prefers_client_field_then_primary(company):
    c1 = Client.objects.create(
        name="A",
        company=company,
        priority="low",
        type="fresh",
        phone_number="  +111  ",
    )
    assert resolve_client_sms_phone(c1).strip() == "+111"

    c2 = Client.objects.create(
        name="B",
        company=company,
        priority="low",
        type="fresh",
        phone_number="",
    )
    ClientPhoneNumber.objects.create(
        client=c2,
        phone_number="07901234567",
        phone_type="mobile",
        is_primary=True,
    )
    assert resolve_client_sms_phone(c2) == "07901234567"


@pytest.mark.django_db
def test_send_skips_when_no_twilio_settings(company):
    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )
    with patch("twilio.rest.Client") as mock_twilio:
        send_lead_created_welcome_sms(client.pk)
        mock_twilio.assert_not_called()


@pytest.mark.django_db
def test_send_skips_when_lead_created_disabled(company):
    tw = TwilioSettings.objects.create(
        company=company,
        is_enabled=True,
        account_sid="ACtest",
        twilio_number="+15551230001",
        lead_created_sms_enabled=False,
        lead_created_sms_template="Hi [name]",
    )
    tw.set_auth_token("secret")
    tw.save(update_fields=["auth_token"])

    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )
    with patch("twilio.rest.Client") as mock_twilio:
        send_lead_created_welcome_sms(client.pk)
        mock_twilio.assert_not_called()


@pytest.mark.django_db
def test_send_skips_empty_template(company):
    tw = TwilioSettings.objects.create(
        company=company,
        is_enabled=True,
        account_sid="ACtest",
        twilio_number="+15551230001",
        lead_created_sms_enabled=True,
        lead_created_sms_template="   ",
    )
    tw.set_auth_token("secret")
    tw.save(update_fields=["auth_token"])

    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )
    with patch("twilio.rest.Client") as mock_twilio:
        send_lead_created_welcome_sms(client.pk)
        mock_twilio.assert_not_called()


@pytest.mark.django_db
def test_send_skips_when_no_phone(company):
    tw = TwilioSettings.objects.create(
        company=company,
        is_enabled=True,
        account_sid="ACtest",
        twilio_number="+15551230001",
        lead_created_sms_enabled=True,
    )
    tw.set_auth_token("secret")
    tw.save(update_fields=["auth_token"])

    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="",
    )
    with patch("twilio.rest.Client") as mock_twilio:
        send_lead_created_welcome_sms(client.pk)
        mock_twilio.assert_not_called()


@pytest.mark.django_db
def test_send_skips_when_monthly_quota_exceeded(company, monkeypatch):
    tw = TwilioSettings.objects.create(
        company=company,
        is_enabled=True,
        account_sid="ACtest",
        twilio_number="+15551230001",
        lead_created_sms_enabled=True,
        lead_created_sms_template="Hi [name]",
    )
    tw.set_auth_token("secret")
    tw.save(update_fields=["auth_token"])

    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )

    def boom(*args, **kwargs):
        raise ValidationError("quota")

    monkeypatch.setattr(
        "integrations.services.lead_created_sms.require_monthly_usage",
        boom,
    )
    with patch("twilio.rest.Client") as mock_twilio:
        send_lead_created_welcome_sms(client.pk)
        mock_twilio.assert_not_called()


@pytest.mark.django_db
def test_send_creates_message_and_calls_twilio(company):
    tw = TwilioSettings.objects.create(
        company=company,
        is_enabled=True,
        account_sid="ACtest",
        twilio_number="+15551230001",
        lead_created_sms_enabled=True,
        lead_created_sms_template="Hello [first_name]",
    )
    tw.set_auth_token("secret")
    tw.save(update_fields=["auth_token"])

    client = Client.objects.create(
        name="Sam Smith",
        company=company,
        priority="low",
        type="fresh",
        phone_number="07901112233",
    )

    mock_msg = SimpleNamespace(sid="SMabc123")
    with patch("twilio.rest.Client") as mock_client_cls:
        mock_client_cls.return_value.messages.create.return_value = mock_msg
        send_lead_created_welcome_sms(client.pk)
        mock_client_cls.return_value.messages.create.assert_called_once()
        call_kw = mock_client_cls.return_value.messages.create.call_args[1]
        assert "Sam" in call_kw["body"]
        assert call_kw["to"].startswith("+")

    rec = LeadSMSMessage.objects.filter(client=client).first()
    assert rec is not None
    assert rec.twilio_sid == "SMabc123"
    assert rec.created_by_id is None
    assert "Sam" in rec.body


@pytest.mark.django_db
def test_send_after_phones_like_post_commit_order(company):
    """After commit, phone may exist only on ClientPhoneNumber (same as serializer.create order)."""
    tw = TwilioSettings.objects.create(
        company=company,
        is_enabled=True,
        account_sid="ACtest",
        twilio_number="+15551230001",
        lead_created_sms_enabled=True,
        lead_created_sms_template="Hi [first_name]",
    )
    tw.set_auth_token("secret")
    tw.save(update_fields=["auth_token"])

    client = Client.objects.create(
        name="Pat Lee",
        company=company,
        priority="low",
        type="fresh",
        phone_number="",
    )
    ClientPhoneNumber.objects.create(
        client=client,
        phone_number="0790555666",
        phone_type="mobile",
        is_primary=True,
    )

    mock_msg = SimpleNamespace(sid="SMcommit1")
    with patch("twilio.rest.Client") as mock_client_cls:
        mock_client_cls.return_value.messages.create.return_value = mock_msg
        send_lead_created_welcome_sms(client.pk)

    rec = LeadSMSMessage.objects.filter(client_id=client.pk).first()
    assert rec is not None
    assert "Pat" in (rec.body or "")


def test_schedule_registers_transaction_on_commit(monkeypatch):
    on_commit = MagicMock()
    monkeypatch.setattr(
        "integrations.services.lead_created_sms.transaction.on_commit",
        on_commit,
    )
    schedule_lead_created_welcome_sms(99)
    on_commit.assert_called_once()
