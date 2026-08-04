"""Tests for automated welcome WhatsApp template on new Client (lead) creation."""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import ValidationError

from crm.models import Client
from integrations.models import (
    LeadWhatsAppMessage,
    MessageSendSource,
    MessageTemplate,
    TwilioSettings,
)
from integrations.services.lead_created_whatsapp import (
    schedule_lead_created_welcome_whatsapp,
    send_lead_created_welcome_whatsapp,
)


def _approved_template(company, **kwargs):
    defaults = {
        "company": company,
        "name": "welcome_tpl",
        "channel_type": MessageTemplate.CHANNEL_WHATSAPP_API,
        "content": "Hello [Customer Name]",
        "meta_status": "APPROVED",
        "language": "en_US",
    }
    defaults.update(kwargs)
    return MessageTemplate.objects.create(**defaults)


def _enable_whatsapp_welcome(company, template):
    return TwilioSettings.objects.create(
        company=company,
        is_enabled=False,
        lead_created_whatsapp_enabled=True,
        lead_created_whatsapp_template=template,
    )


@pytest.mark.django_db
def test_send_skips_when_no_twilio_settings(company):
    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )
    with patch(
        "integrations.services.lead_created_whatsapp.send_approved_whatsapp_template"
    ) as mock_send:
        send_lead_created_welcome_whatsapp(client.pk)
        mock_send.assert_not_called()


@pytest.mark.django_db
def test_send_skips_when_whatsapp_welcome_disabled(company):
    tpl = _approved_template(company)
    TwilioSettings.objects.create(
        company=company,
        lead_created_whatsapp_enabled=False,
        lead_created_whatsapp_template=tpl,
    )
    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )
    with patch(
        "integrations.services.lead_created_whatsapp.send_approved_whatsapp_template"
    ) as mock_send:
        send_lead_created_welcome_whatsapp(client.pk)
        mock_send.assert_not_called()


@pytest.mark.django_db
def test_send_skips_when_no_phone(company):
    tpl = _approved_template(company)
    _enable_whatsapp_welcome(company, tpl)
    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="",
    )
    with patch(
        "integrations.services.lead_created_whatsapp.is_integration_allowed",
        return_value=True,
    ), patch(
        "integrations.services.lead_created_whatsapp.require_monthly_usage",
    ), patch(
        "integrations.services.lead_created_whatsapp.send_approved_whatsapp_template"
    ) as mock_send:
        send_lead_created_welcome_whatsapp(client.pk)
        mock_send.assert_not_called()


@pytest.mark.django_db
def test_send_skips_non_approved_template(company):
    tpl = _approved_template(company, meta_status="PENDING")
    _enable_whatsapp_welcome(company, tpl)
    client = Client.objects.create(
        name="Sam Smith",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )

    mock_wa = MagicMock()
    mock_wa.phone_number_id = "pnid1"
    mock_wa.get_access_token.return_value = "token"
    mock_wa.integration_account_id = None

    with patch(
        "integrations.services.lead_created_whatsapp.is_integration_allowed",
        return_value=True,
    ), patch(
        "integrations.services.lead_created_whatsapp.require_monthly_usage",
    ), patch(
        "integrations.services.whatsapp_template_send.resolve_whatsapp_account_for_api",
        return_value=(mock_wa, None),
    ), patch(
        "integrations.services.whatsapp_template_send.requests.post"
    ) as mock_post, patch(
        "integrations.services.lead_created_whatsapp.increment_monthly_usage"
    ) as mock_inc:
        send_lead_created_welcome_whatsapp(client.pk)
        mock_post.assert_not_called()
        mock_inc.assert_not_called()

    assert LeadWhatsAppMessage.objects.filter(client=client).count() == 0


@pytest.mark.django_db
def test_send_skips_when_quota_exceeded(company):
    tpl = _approved_template(company)
    _enable_whatsapp_welcome(company, tpl)
    client = Client.objects.create(
        name="X",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )

    def boom(*args, **kwargs):
        raise ValidationError("quota")

    with patch(
        "integrations.services.lead_created_whatsapp.is_integration_allowed",
        return_value=True,
    ), patch(
        "integrations.services.lead_created_whatsapp.require_monthly_usage",
        boom,
    ), patch(
        "integrations.services.lead_created_whatsapp.send_approved_whatsapp_template"
    ) as mock_send:
        send_lead_created_welcome_whatsapp(client.pk)
        mock_send.assert_not_called()


@pytest.mark.django_db
def test_send_creates_message_and_calls_graph(company):
    tpl = _approved_template(company)
    _enable_whatsapp_welcome(company, tpl)
    client = Client.objects.create(
        name="Sam Smith",
        company=company,
        priority="low",
        type="fresh",
        phone_number="+15550001111",
    )

    mock_wa = MagicMock()
    mock_wa.phone_number_id = "pnid1"
    mock_wa.get_access_token.return_value = "token"
    mock_wa.integration_account_id = None

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"messages": [{"id": "wamid.abc"}]}

    with patch(
        "integrations.services.lead_created_whatsapp.is_integration_allowed",
        return_value=True,
    ), patch(
        "integrations.services.lead_created_whatsapp.require_monthly_usage",
    ), patch(
        "integrations.services.lead_created_whatsapp.increment_monthly_usage"
    ) as mock_inc, patch(
        "integrations.services.whatsapp_template_send.resolve_whatsapp_account_for_api",
        return_value=(mock_wa, None),
    ), patch(
        "integrations.services.whatsapp_template_send.requests.post",
        return_value=mock_resp,
    ) as mock_post:
        send_lead_created_welcome_whatsapp(client.pk)
        mock_post.assert_called_once()
        mock_inc.assert_called_once()

    rec = LeadWhatsAppMessage.objects.filter(client=client).first()
    assert rec is not None
    assert rec.created_by_id is None
    assert rec.send_source == MessageSendSource.AUTO_WELCOME
    assert rec.whatsapp_message_id == "wamid.abc"
    assert "Sam" in (rec.body or "") or "Hello" in (rec.body or "")


def test_schedule_registers_transaction_on_commit(monkeypatch):
    on_commit = MagicMock()
    monkeypatch.setattr(
        "integrations.services.lead_created_whatsapp.transaction.on_commit",
        on_commit,
    )
    schedule_lead_created_welcome_whatsapp(99)
    on_commit.assert_called_once()
