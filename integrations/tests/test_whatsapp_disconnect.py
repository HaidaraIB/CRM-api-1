"""Disconnect / destroy must clear WhatsAppAccount status and tokens."""

import pytest

from integrations.models import IntegrationAccount, WhatsAppAccount
from integrations.whatsapp_account_sync import (
    disconnect_whatsapp_accounts_for_integration,
    get_connected_whatsapp_account,
)


@pytest.fixture
def whatsapp_connected(company, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Disconnect Test",
        status="connected",
    )
    account.set_access_token("integration-token")
    account.save(update_fields=["access_token"])
    wa = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-disc-1",
        phone_number_id="phone-disc-1",
        display_phone_number="15550001111",
        status="connected",
        integration_account=account,
    )
    wa.set_access_token("wa-token")
    wa.save(update_fields=["access_token"])
    return account, wa


@pytest.mark.django_db
def test_helper_clears_status_and_token(whatsapp_connected):
    account, wa = whatsapp_connected
    assert get_connected_whatsapp_account(account.company) is not None

    n = disconnect_whatsapp_accounts_for_integration(account)
    assert n == 1
    wa.refresh_from_db()
    assert wa.status == "disconnected"
    assert wa.get_access_token() is None
    assert wa.integration_account_id is None
    assert get_connected_whatsapp_account(account.company) is None


@pytest.mark.django_db
def test_helper_clears_orphaned_connected_rows(company, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Orphan Parent",
        status="disconnected",
    )
    orphan = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-orphan",
        phone_number_id="phone-orphan",
        display_phone_number="15550002222",
        status="connected",
        integration_account=None,
    )
    orphan.set_access_token("orphan-token")
    orphan.save(update_fields=["access_token"])
    assert orphan.status == "connected"
    assert orphan.get_access_token() == "orphan-token"

    n = disconnect_whatsapp_accounts_for_integration(account)
    assert n == 1
    orphan.refresh_from_db()
    assert orphan.status == "disconnected"
    assert orphan.get_access_token() is None
    assert get_connected_whatsapp_account(company) is None


@pytest.mark.django_db
def test_disconnect_endpoint_clears_whatsapp(authenticated_admin, whatsapp_connected):
    account, wa = whatsapp_connected
    response = authenticated_admin.post(f"/api/v1/integrations/accounts/{account.id}/disconnect/")
    assert response.status_code == 200, response.content
    account.refresh_from_db()
    wa.refresh_from_db()
    assert account.status == "disconnected"
    assert account.get_access_token() is None
    assert wa.status == "disconnected"
    assert wa.get_access_token() is None
    assert get_connected_whatsapp_account(account.company) is None


@pytest.mark.django_db
def test_destroy_does_not_leave_sendable_whatsapp(authenticated_admin, whatsapp_connected):
    account, wa = whatsapp_connected
    company = account.company
    response = authenticated_admin.delete(f"/api/v1/integrations/accounts/{account.id}/")
    assert response.status_code in (200, 204), response.content
    assert not IntegrationAccount.objects.filter(id=account.id).exists()
    wa.refresh_from_db()
    assert wa.status == "disconnected"
    assert wa.get_access_token() is None
    assert get_connected_whatsapp_account(company) is None


@pytest.mark.django_db
def test_get_connected_clears_orphans_when_integration_disconnected(company, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Disc",
        status="disconnected",
    )
    orphan = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-o2",
        phone_number_id="phone-o2",
        display_phone_number="15550003333",
        status="connected",
        integration_account=None,
    )
    orphan.set_access_token("still-valid")
    orphan.save(update_fields=["access_token"])

    assert get_connected_whatsapp_account(company) is None
    orphan.refresh_from_db()
    assert orphan.status == "disconnected"
    assert orphan.get_access_token() is None


@pytest.mark.django_db
def test_resolve_crm_sender_does_not_disconnect_inbox_number(company, plan, subscription):
    """Inbox-only companies hit CRM sender resolution from Calls and templates."""
    from integrations.models import WhatsAppPurpose
    from integrations.whatsapp_account_sync import resolve_whatsapp_account_for_api

    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    inbox_account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp_inbox",
        name="Inbox only",
        status="connected",
    )
    inbox = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-inbox-keep",
        phone_number_id="inbox-keep-1",
        display_phone_number="9647700001111",
        status="connected",
        purpose=WhatsAppPurpose.INBOX,
        integration_account=inbox_account,
    )
    inbox.set_access_token("inbox-token")
    inbox.save(update_fields=["access_token"])

    wa, err = resolve_whatsapp_account_for_api(company)
    assert wa is None
    assert err == "no_connected_whatsapp_number"
    inbox.refresh_from_db()
    assert inbox.status == "connected"
    assert inbox.integration_account_id == inbox_account.id
    assert inbox.get_access_token() == "inbox-token"


@pytest.mark.django_db
def test_expired_crm_integration_does_not_disconnect_numbers(company, plan, subscription):
    from integrations.models import WhatsAppPurpose
    from integrations.whatsapp_account_sync import resolve_whatsapp_account_for_api

    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    crm_account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="CRM expired",
        status="expired",
    )
    crm_account.set_access_token("crm-token")
    crm_account.save(update_fields=["access_token"])
    crm = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-crm-exp",
        phone_number_id="crm-exp-1",
        display_phone_number="9647700002222",
        status="connected",
        purpose=WhatsAppPurpose.CRM,
        integration_account=crm_account,
    )
    crm.set_access_token("crm-row-token")
    crm.save(update_fields=["access_token"])
    inbox = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-inbox-exp",
        phone_number_id="inbox-exp-1",
        display_phone_number="9647700003333",
        status="connected",
        purpose=WhatsAppPurpose.INBOX,
    )
    inbox.set_access_token("inbox-row-token")
    inbox.save(update_fields=["access_token"])

    resolve_whatsapp_account_for_api(company)
    crm.refresh_from_db()
    inbox.refresh_from_db()
    assert crm.status == "connected"
    assert crm.get_access_token() == "crm-row-token"
    assert inbox.status == "connected"
    assert inbox.get_access_token() == "inbox-row-token"


@pytest.mark.django_db
def test_account_update_without_waba_id_does_not_disconnect(company):
    from integrations.whatsapp_webhook import process_account_update

    wa = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-keep",
        phone_number_id="phone-keep-partner",
        status="connected",
    )
    process_account_update({"event": "PARTNER_REMOVED"}, waba_id=None)
    wa.refresh_from_db()
    assert wa.status == "connected"
