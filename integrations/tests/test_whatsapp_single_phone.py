"""Single WhatsApp phone per integration (Embedded Signup pick)."""

from unittest.mock import MagicMock, patch

import pytest

from integrations.models import IntegrationAccount, WhatsAppAccount
from integrations.whatsapp_account_sync import (
    disconnect_extra_whatsapp_phones_for_integration,
    get_connected_whatsapp_account,
    is_meta_provided_test_number,
    sync_whatsapp_accounts_from_integration,
    upsert_whatsapp_account_from_embedded_signup,
)


@pytest.fixture
def wa_integration(company, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Single Phone",
        status="connected",
        metadata={},
    )
    account.set_access_token("integration-token")
    account.save(update_fields=["access_token"])
    return account


def test_is_meta_provided_test_number():
    assert is_meta_provided_test_number(display_phone_number="+1 555-904-2129")
    assert is_meta_provided_test_number(display_phone_number="15559042129")
    assert not is_meta_provided_test_number(display_phone_number="+964 771 595 2996")
    assert is_meta_provided_test_number(phone_number_id="seed_demo_1")


@pytest.mark.django_db
def test_disconnect_extras_keeps_wizard_phone(wa_integration, company):
    keep = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-real",
        phone_number_id="521972671007854",
        display_phone_number="+964 771 595 2996",
        status="connected",
        integration_account=wa_integration,
    )
    keep.set_access_token("tok")
    keep.save(update_fields=["access_token"])
    extra = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-555",
        phone_number_id="1256143954244526",
        display_phone_number="+1 555-904-2129",
        status="connected",
        integration_account=wa_integration,
    )
    extra.set_access_token("tok2")
    extra.save(update_fields=["access_token"])

    n = disconnect_extra_whatsapp_phones_for_integration(wa_integration, keep.phone_number_id)
    assert n == 1
    keep.refresh_from_db()
    extra.refresh_from_db()
    assert keep.status == "connected"
    assert extra.status == "disconnected"
    assert extra.get_access_token() is None


@pytest.mark.django_db
@patch("integrations.whatsapp_account_sync._fetch_phone_profile")
def test_upsert_embedded_signup_disconnects_other_phones(mock_profile, wa_integration, company):
    mock_profile.return_value = {
        "display": "+964 771 595 2996",
        "verified_name": "Biz",
        "name_status": "APPROVED",
    }
    old = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-555",
        phone_number_id="1256143954244526",
        display_phone_number="+1 555-904-2129",
        status="connected",
        integration_account=wa_integration,
    )
    old.set_access_token("old")
    old.save(update_fields=["access_token"])

    wa = upsert_whatsapp_account_from_embedded_signup(
        wa_integration,
        "new-token",
        waba_id="552144907984166",
        phone_number_id="521972671007854",
    )
    assert wa.phone_number_id == "521972671007854"
    assert wa.status == "connected"
    assert (wa_integration.metadata or {}).get("phone_number_id") == "521972671007854"
    old.refresh_from_db()
    assert old.status == "disconnected"


@pytest.mark.django_db
@patch("integrations.whatsapp_account_sync._fetch_phone_profile")
@patch("integrations.whatsapp_account_sync.get_oauth_handler")
def test_sync_pins_metadata_phone_and_drops_555(mock_handler, mock_profile, wa_integration, company):
    mock_profile.return_value = {
        "display": "+964 771 595 2996",
        "verified_name": "Biz",
        "name_status": "APPROVED",
    }
    wa_integration.metadata = {
        "waba_id": "552144907984166",
        "phone_number_id": "521972671007854",
    }
    wa_integration.save(update_fields=["metadata"])

    WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-555",
        phone_number_id="1256143954244526",
        display_phone_number="+1 555-904-2129",
        status="connected",
        integration_account=wa_integration,
    )

    handler = MagicMock()
    handler.get_waba_and_phone_numbers.return_value = [
        {
            "waba_id": "956081710610674",
            "business_id": None,
            "phone_numbers": [
                {
                    "id": "1256143954244526",
                    "display_phone_number": "+1 555-904-2129",
                    "name_status": "PENDING",
                },
            ],
        },
        {
            "waba_id": "552144907984166",
            "business_id": None,
            "phone_numbers": [
                {
                    "id": "521972671007854",
                    "display_phone_number": "+964 771 595 2996",
                    "name_status": "APPROVED",
                },
            ],
        },
    ]
    mock_handler.return_value = handler

    synced = sync_whatsapp_accounts_from_integration(wa_integration, "tok")
    assert synced == 1
    wa_integration.refresh_from_db()
    assert (wa_integration.metadata or {}).get("phone_number_id") == "521972671007854"

    connected = list(
        WhatsAppAccount.objects.filter(
            company=company, integration_account=wa_integration, status="connected"
        )
    )
    assert len(connected) == 1
    assert connected[0].phone_number_id == "521972671007854"

    chosen = get_connected_whatsapp_account(company)
    assert chosen is not None
    assert chosen.phone_number_id == "521972671007854"


@pytest.mark.django_db
def test_get_connected_prefers_metadata_over_newer_555(wa_integration, company):
    wa_integration.metadata = {"phone_number_id": "521972671007854", "waba_id": "w1"}
    wa_integration.save(update_fields=["metadata"])

    real = WhatsAppAccount.objects.create(
        company=company,
        waba_id="w1",
        phone_number_id="521972671007854",
        display_phone_number="+964 771 595 2996",
        status="connected",
        integration_account=wa_integration,
    )
    real.set_access_token("real-tok")
    real.save(update_fields=["access_token"])

    fake = WhatsAppAccount.objects.create(
        company=company,
        waba_id="w2",
        phone_number_id="1256143954244526",
        display_phone_number="+1 555-904-2129",
        status="connected",
        integration_account=wa_integration,
    )
    fake.set_access_token("fake-tok")
    fake.save(update_fields=["access_token"])
    # Make 555 look "newer" like the bug report.
    WhatsAppAccount.objects.filter(pk=fake.pk).update(
        updated_at=real.updated_at.replace(microsecond=real.updated_at.microsecond + 1)
        if real.updated_at.microsecond < 999999
        else real.updated_at
    )

    chosen = get_connected_whatsapp_account(company)
    assert chosen.phone_number_id == "521972671007854"
