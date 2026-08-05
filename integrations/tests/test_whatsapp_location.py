"""WhatsApp location message send + inbound storage tests."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from crm.models import Client
from integrations.models import IntegrationAccount, LeadWhatsAppMessage, WhatsAppAccount
from integrations.services.whatsapp_coexistence import (
    apply_location_fields_to_message,
    extract_whatsapp_message_body,
)
from integrations.whatsapp_webhook import process_whatsapp_message


@pytest.fixture
def whatsapp_setup(company, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Location Test",
        status="connected",
    )
    account.set_access_token("test-token")
    account.save(update_fields=["access_token"])
    wa = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-loc",
        phone_number_id="phone-loc-1",
        display_phone_number="15550783881",
        status="connected",
        integration_account=account,
    )
    wa.set_access_token("test-token")
    wa.save(update_fields=["access_token"])
    return account, wa


def test_extract_location_body_with_name_and_address():
    body = extract_whatsapp_message_body(
        {
            "type": "location",
            "location": {
                "latitude": 33.3,
                "longitude": 44.4,
                "name": "Clinic",
                "address": "Baghdad",
            },
        }
    )
    assert body == "Clinic — Baghdad"


def test_extract_location_body_fallback():
    assert (
        extract_whatsapp_message_body({"type": "location", "location": {"latitude": 1, "longitude": 2}})
        == "[location message]"
    )


def test_apply_location_fields_to_message():
    row = LeadWhatsAppMessage()
    ok = apply_location_fields_to_message(
        row,
        {
            "type": "location",
            "location": {
                "latitude": "33.315241",
                "longitude": "44.366067",
                "name": "HQ",
                "address": "Street 1",
            },
        },
    )
    assert ok is True
    assert row.location_latitude == Decimal("33.315241")
    assert row.location_longitude == Decimal("44.366067")
    assert row.location_name == "HQ"
    assert row.location_address == "Street 1"
    assert row.attachment_kind == "location"
    assert "HQ" in row.body


@pytest.mark.django_db
def test_inbound_location_webhook_stores_coords(whatsapp_setup, company):
    _account, wa = whatsapp_setup
    process_whatsapp_message(
        {
            "from": "16505551234",
            "id": "wamid.loc.inbound.1",
            "timestamp": "1700000000",
            "type": "location",
            "location": {
                "latitude": 33.3,
                "longitude": 44.4,
                "name": "Park",
                "address": "Main St",
            },
        },
        wa.phone_number_id,
    )
    row = LeadWhatsAppMessage.objects.get(whatsapp_message_id="wamid.loc.inbound.1")
    assert row.direction == LeadWhatsAppMessage.DIRECTION_INBOUND
    assert row.attachment_kind == LeadWhatsAppMessage.AttachmentKind.LOCATION
    assert float(row.location_latitude) == pytest.approx(33.3)
    assert float(row.location_longitude) == pytest.approx(44.4)
    assert row.location_name == "Park"
    assert row.location_address == "Main St"
    assert "Park" in row.body


@pytest.mark.django_db
@patch("integrations.views.webhooks_messaging.requests.post")
def test_send_location_happy_path(mock_post, whatsapp_setup, company, admin_user, subscription):
    _account, wa = whatsapp_setup
    lead = Client.objects.create(
        company=company,
        name="Loc Lead",
        phone_number="16505559999",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"messages": [{"id": "wamid.loc.out.1"}]}
    mock_post.return_value = mock_resp

    api = APIClient()
    api.force_authenticate(user=admin_user)
    res = api.post(
        reverse("whatsapp_send_location"),
        {
            "to": "16505559999",
            "client_id": lead.id,
            "latitude": 36.19,
            "longitude": 44.01,
            "name": "Erbil",
            "address": "100m Street",
            "phone_number_id": wa.phone_number_id,
        },
        format="json",
    )
    assert res.status_code == 200, res.content
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert payload["type"] == "location"
    assert payload["location"]["latitude"] == pytest.approx(36.19)
    assert payload["location"]["name"] == "Erbil"

    row = LeadWhatsAppMessage.objects.get(whatsapp_message_id="wamid.loc.out.1")
    assert row.direction == LeadWhatsAppMessage.DIRECTION_OUTBOUND
    assert row.attachment_kind == LeadWhatsAppMessage.AttachmentKind.LOCATION
    assert float(row.location_latitude) == pytest.approx(36.19)
    assert float(row.location_longitude) == pytest.approx(44.01)
    assert row.location_name == "Erbil"
    body = res.json()
    data = body.get("data") or body
    assert data.get("message", {}).get("location_name") == "Erbil"
