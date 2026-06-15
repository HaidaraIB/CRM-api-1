"""Tests for Meta Conversion Leads CAPI integration."""

from unittest.mock import patch

import pytest
from django.utils import timezone

from crm.models import Client
from crm.serializers import ClientSerializer
from integrations.models import IntegrationAccount
from integrations.serializers import IntegrationAccountUpdateSerializer
from integrations.services.meta_conversion_leads import (
    EVENT_RAW_LEAD,
    apply_qualification_status_change,
    build_conversion_lead_event,
    send_conversion_lead_event,
)


def _create_meta_account(company, *, pixel_id="1234567890123", metadata=None):
    account = IntegrationAccount.objects.create(
        company=company,
        platform="meta",
        name="Meta Test",
        status="connected",
        external_account_id="meta-test-account",
        metadata=metadata if metadata is not None else {"pixel_id": pixel_id},
    )
    account.set_access_token("test-access-token")
    account.save(update_fields=["access_token"])
    return account


def _create_meta_client(company, account, **kwargs):
    defaults = {
        "name": "Meta Lead",
        "company": company,
        "priority": "medium",
        "type": "fresh",
        "source": "meta_lead_form",
        "integration_account": account,
        "meta_leadgen_id": "9876543210",
    }
    defaults.update(kwargs)
    return Client.objects.create(**defaults)


@pytest.mark.django_db
@patch("integrations.services.meta_conversion_leads.MetaOAuth.send_conversion_leads_events")
def test_skips_send_if_source_not_meta_lead_form(mock_send, company):
    account = _create_meta_account(company)
    client = _create_meta_client(company, account, source="manual")

    result = send_conversion_lead_event(client, EVENT_RAW_LEAD)

    mock_send.assert_not_called()
    assert result["success"] is False
    assert result["error_key"] == "metaQualificationErrorNotMetaLead"


@pytest.mark.django_db
@patch("integrations.services.meta_conversion_leads.MetaOAuth.send_conversion_leads_events")
def test_skips_send_if_no_leadgen_id(mock_send, company):
    account = _create_meta_account(company)
    client = _create_meta_client(company, account, meta_leadgen_id=None)

    result = send_conversion_lead_event(client, EVENT_RAW_LEAD)

    mock_send.assert_not_called()
    assert result["success"] is False
    assert result["error_key"] == "metaQualificationErrorNoLeadgenId"


@pytest.mark.django_db
@patch("integrations.services.meta_conversion_leads.MetaOAuth.send_conversion_leads_events")
def test_skips_send_if_no_pixel_configured(mock_send, company):
    account = _create_meta_account(company, metadata={})
    client = _create_meta_client(company, account)

    result = send_conversion_lead_event(client, EVENT_RAW_LEAD)

    mock_send.assert_not_called()
    assert result["success"] is False
    assert result["error_key"] == "metaQualificationErrorNoPixelConfigured"


@pytest.mark.django_db
@patch("integrations.services.meta_conversion_leads.MetaOAuth.send_conversion_leads_events")
def test_successful_qualification_event(mock_send, company):
    mock_send.return_value = {"events_received": 1}
    account = _create_meta_account(company)
    client = _create_meta_client(company, account)

    apply_qualification_status_change(client, "qualified", None)
    client.refresh_from_db()

    mock_send.assert_called_once()
    assert client.meta_qualification_sent_at is not None
    assert client.meta_qualification_error is None


@pytest.mark.django_db
@patch("integrations.services.meta_conversion_leads.MetaOAuth.send_conversion_leads_events")
def test_failed_qualification_event(mock_send, company):
    mock_send.side_effect = Exception("HTTP 500")
    account = _create_meta_account(company)
    client = _create_meta_client(company, account)
    client.meta_qualification_sent_at = None
    client.save(update_fields=["meta_qualification_sent_at"])

    apply_qualification_status_change(client, "qualified", None)
    client.refresh_from_db()

    assert client.meta_qualification_sent_at is None
    assert client.meta_qualification_error == "metaQualificationErrorSendFailed"
    serialized = ClientSerializer(client).data["meta_qualification_error"]
    assert serialized["key"] == "metaQualificationErrorSendFailed"


@pytest.mark.django_db
@patch("integrations.services.meta_conversion_leads.MetaOAuth.send_conversion_leads_events")
def test_no_duplicate_send_same_status(mock_send, company):
    account = _create_meta_account(company)
    client = _create_meta_client(company, account, meta_qualification_status="qualified")

    apply_qualification_status_change(client, "qualified", "qualified")

    mock_send.assert_not_called()


@pytest.mark.django_db
@patch("integrations.services.meta_conversion_leads.MetaOAuth.send_conversion_leads_events")
def test_null_reset_clears_sent_at(mock_send, company):
    account = _create_meta_account(company)
    client = _create_meta_client(company, account, meta_qualification_status="qualified")
    client.meta_qualification_sent_at = timezone.now()
    client.meta_qualification_error = "metaQualificationErrorSendFailed"
    client.save(update_fields=["meta_qualification_sent_at", "meta_qualification_error"])

    apply_qualification_status_change(client, None, "qualified")
    client.refresh_from_db()

    mock_send.assert_not_called()
    assert client.meta_qualification_sent_at is None
    assert client.meta_qualification_error is None


def test_event_id_is_deterministic():
    event1 = build_conversion_lead_event(
        client_id=42,
        event_name=EVENT_RAW_LEAD,
        leadgen_id="12345",
        event_time=1700000000,
    )
    event2 = build_conversion_lead_event(
        client_id=42,
        event_name=EVENT_RAW_LEAD,
        leadgen_id="12345",
        event_time=1700000000,
    )

    assert event1["event_id"] == event2["event_id"]
    assert event1["event_id"] == "42-Raw Lead-1700000000"


@pytest.mark.django_db
def test_pixel_id_validation_rejects_non_numeric(company):
    account = _create_meta_account(company)

    serializer = IntegrationAccountUpdateSerializer(
        instance=account,
        data={"pixel_id": "abc123"},
        partial=True,
    )

    assert not serializer.is_valid()
    assert "pixel_id" in serializer.errors


@pytest.mark.django_db
def test_pixel_id_validation_strips_whitespace(company):
    account = _create_meta_account(company, metadata={})

    serializer = IntegrationAccountUpdateSerializer(
        instance=account,
        data={"pixel_id": " 1234567890 "},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    account = serializer.save()
    account.refresh_from_db()

    assert account.metadata["pixel_id"] == "1234567890"
