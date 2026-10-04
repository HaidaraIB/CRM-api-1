"""
WhatsApp number ownership: one number = one owner (platform / company CRM / company inbox).

* Connecting a number someone else owns is rejected with a clear error key.
* Inbound messages and statuses go to the number's single owner.
* Legacy shared numbers are cleaned up by `resolve_whatsapp_number_conflicts`
  (platform keeps its number, otherwise earliest connection keeps it); the loser
  is disconnected, sees the reason, and its owner is notified.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from companies.models import AdminTenantWhatsAppMessage
from integrations.models import (
    IntegrationAccount,
    LeadWhatsAppMessage,
    SocialMessage,
    WhatsAppAccount,
    WhatsAppInboxNumber,
)
from integrations.services.whatsapp_number_ownership import (
    OWNER_CRM,
    OWNER_INBOX,
    OWNER_PLATFORM,
    WhatsAppNumberConflictError,
    assert_number_available,
    find_number_conflicts,
    owners_for_phone_number_id,
    pick_keeper,
)
from integrations.whatsapp_webhook import process_whatsapp_message, process_whatsapp_status_update

pytestmark = pytest.mark.django_db

PLATFORM_PID = "platform_pid_1"
OWNER_PHONE = "9647747347772"
STRANGER_PHONE = "9647715952996"


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def platform_pid(db):
    from settings.models import PlatformWhatsAppSettings

    row = PlatformWhatsAppSettings.get_settings()
    row.phone_number_id = PLATFORM_PID
    row.save()
    return PLATFORM_PID


@pytest.fixture
def owner_company(other_company, other_owner_user):
    """Company whose owner's phone is OWNER_PHONE (target of the admin↔owner thread)."""
    other_owner_user.phone = f"+{OWNER_PHONE}"
    other_owner_user.save(update_fields=["phone"])
    return other_company


@pytest.fixture
def wa_enabled(plan, subscription):
    plan.features = {
        **(plan.features or {}),
        "integration_whatsapp": True,
        "integration_meta_inbox": True,
    }
    plan.save(update_fields=["features"])


def _age(row, days):
    type(row).objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days))
    row.refresh_from_db()
    return row


def _crm_account(company, pid, status="connected", days_old=0):
    acc = IntegrationAccount.objects.create(
        company=company, platform="whatsapp", name="CRM WA", status="connected"
    )
    wa = WhatsAppAccount.objects.create(
        company=company,
        integration_account=acc,
        waba_id=f"waba_{pid}",
        phone_number_id=pid,
        display_phone_number="+964 700 000 0000",
        status=status,
    )
    wa.set_access_token("tok")
    wa.save()
    return _age(wa, days_old)


def _inbox_number(company, pid, days_old=0):
    acc = IntegrationAccount.objects.create(
        company=company, platform="whatsapp_inbox", name="Inbox", status="connected"
    )
    row = WhatsAppInboxNumber.objects.create(
        company=company,
        integration_account=acc,
        waba_id=f"waba_inbox_{pid}",
        phone_number_id=pid,
        status="connected",
    )
    row.set_access_token("tok")
    row.save()
    return _age(row, days_old)


def _inbound(sender, wamid):
    return {
        "from": sender,
        "id": wamid,
        "timestamp": str(int(timezone.now().timestamp())),
        "type": "text",
        "text": {"body": "reply"},
    }


def _crm_inbound(wamid):
    return LeadWhatsAppMessage.objects.filter(
        whatsapp_message_id=wamid, direction=LeadWhatsAppMessage.DIRECTION_INBOUND
    ).first()


# --- inbound routing: single owner ------------------------------------------------


def test_platform_number_goes_to_admin_thread(platform_pid, owner_company):
    process_whatsapp_message(_inbound(OWNER_PHONE, "wamid.a1"), platform_pid)
    assert AdminTenantWhatsAppMessage.objects.filter(company=owner_company).count() == 1


@patch("integrations.services.whatsapp_push.notify_whatsapp_inbound")
def test_crm_number_goes_to_crm(_p, company, wa_enabled):
    _crm_account(company, "solo_pid")
    process_whatsapp_message(_inbound(STRANGER_PHONE, "wamid.b1"), "solo_pid")
    assert _crm_inbound("wamid.b1").client.company_id == company.id


def test_inbox_number_goes_to_inbox(company, wa_enabled):
    _inbox_number(company, "inbox_pid")
    process_whatsapp_message(_inbound(STRANGER_PHONE, "wamid.c1"), "inbox_pid")
    assert SocialMessage.objects.filter(external_message_id="wamid.c1").exists()
    assert _crm_inbound("wamid.c1") is None


def test_unknown_number_is_ignored(db):
    process_whatsapp_message(_inbound(STRANGER_PHONE, "wamid.d1"), "nobody_pid")
    assert _crm_inbound("wamid.d1") is None


# --- a conflict that hasn't been cleaned up yet goes to the would-be keeper ---------


def test_unresolved_conflict_routes_to_keeper(company, other_company, wa_enabled):
    pid = "shared_pid"
    _crm_account(company, pid, days_old=10)  # connected first → keeper
    _inbox_number(other_company, pid, days_old=1)
    with patch("integrations.services.whatsapp_push.notify_whatsapp_inbound"):
        process_whatsapp_message(_inbound(STRANGER_PHONE, "wamid.e1"), pid)
    assert _crm_inbound("wamid.e1").client.company_id == company.id
    assert not SocialMessage.objects.filter(external_message_id="wamid.e1").exists()


def test_status_follows_owner(company, wa_enabled):
    from integrations.services.whatsapp_client import ensure_client_for_whatsapp_phone

    _crm_account(company, "st_pid")
    client = ensure_client_for_whatsapp_phone(company, STRANGER_PHONE)
    out = LeadWhatsAppMessage.objects.create(
        client=client,
        phone_number=STRANGER_PHONE,
        body="hi",
        direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
        whatsapp_message_id="wamid.st1",
        delivery_status="sent",
    )
    process_whatsapp_status_update({"id": "wamid.st1", "status": "delivered"}, "st_pid")
    out.refresh_from_db()
    assert out.delivery_status == "delivered"


# --- connect rules -------------------------------------------------------------------


def test_platform_number_cannot_be_connected(company, platform_pid):
    for kind in (OWNER_CRM, OWNER_INBOX):
        with pytest.raises(WhatsAppNumberConflictError) as exc:
            assert_number_available(company.id, platform_pid, kind)
        assert exc.value.error_key == "whatsapp_number_is_platform_number"


def test_reconnecting_own_number_is_allowed(company):
    _crm_account(company, "mine")
    assert_number_available(company.id, "mine", OWNER_CRM)


def test_legacy_shared_number_cannot_reconnect(company, platform_pid):
    """No exemptions: a company holding the platform number must hand it back."""
    _crm_account(company, platform_pid)
    with pytest.raises(WhatsAppNumberConflictError) as exc:
        assert_number_available(company.id, platform_pid, OWNER_CRM)
    assert exc.value.error_key == "whatsapp_number_is_platform_number"


def test_number_of_other_company_is_rejected(company, other_company):
    _crm_account(company, "taken_crm")
    _inbox_number(company, "taken_inbox")
    for pid in ("taken_crm", "taken_inbox"):
        for kind in (OWNER_CRM, OWNER_INBOX):
            with pytest.raises(WhatsAppNumberConflictError) as exc:
                assert_number_available(other_company.id, pid, kind)
            assert exc.value.error_key == "whatsapp_number_in_use_by_other_company"


def test_same_company_crm_vs_inbox_keys(company):
    _crm_account(company, "crm_pid")
    _inbox_number(company, "inbox_pid")
    with pytest.raises(WhatsAppNumberConflictError) as exc:
        assert_number_available(company.id, "crm_pid", OWNER_INBOX)
    assert exc.value.error_key == "whatsapp_inbox_number_in_use_by_crm"
    with pytest.raises(WhatsAppNumberConflictError) as exc:
        assert_number_available(company.id, "inbox_pid", OWNER_CRM)
    assert exc.value.error_key == "whatsapp_number_in_use_by_inbox"


def test_disconnected_number_of_other_company_is_free(company, other_company):
    _crm_account(company, "freed_pid", status="disconnected")
    assert_number_available(other_company.id, "freed_pid", OWNER_CRM)


def test_pick_keeper(company, other_company, platform_pid):
    _crm_account(company, platform_pid, days_old=100)
    assert pick_keeper(owners_for_phone_number_id(platform_pid)).kind == OWNER_PLATFORM

    _crm_account(company, "p2", days_old=1)
    _inbox_number(other_company, "p2", days_old=5)
    assert pick_keeper(owners_for_phone_number_id("p2")).kind == OWNER_INBOX


def test_platform_settings_reject_company_number(company):
    from settings.serializers import PlatformWhatsAppSettingsSerializer
    from settings.models import PlatformWhatsAppSettings

    _crm_account(company, "company_pid")
    ser = PlatformWhatsAppSettingsSerializer(
        instance=PlatformWhatsAppSettings.get_settings(),
        data={"phone_number_id": "company_pid"},
        partial=True,
    )
    assert not ser.is_valid()
    assert "phone_number_id" in ser.errors


# --- cleanup command ---------------------------------------------------------------


def _run(*args):
    out = StringIO()
    call_command("resolve_whatsapp_number_conflicts", *args, stdout=out)
    return out.getvalue()


@patch("notifications.services.NotificationService.send_notification")
def test_cleanup_dry_run_changes_nothing(send, company, platform_pid):
    wa = _crm_account(company, platform_pid)
    out = _run()
    assert "DRY RUN" in out and platform_pid in out
    wa.refresh_from_db()
    assert wa.status == "connected"
    send.assert_not_called()


@patch("notifications.services.NotificationService.send_notification")
def test_cleanup_platform_keeps_its_number(send, company, owner_user, platform_pid):
    wa = _crm_account(company, platform_pid)
    integration = wa.integration_account

    _run("--apply")

    wa.refresh_from_db()
    integration.refresh_from_db()
    assert wa.status == "disconnected" and wa.get_access_token() in (None, "")
    assert integration.status == "error"
    assert integration.metadata["number_conflict_key"] == "whatsapp_number_is_platform_number"
    send.assert_called_once()
    assert send.call_args.kwargs["user"] == owner_user
    assert send.call_args.kwargs["data"]["error_key"] == "whatsapp_number_is_platform_number"
    assert find_number_conflicts() == []
    # Second run is a no-op.
    assert "Nothing to do" in _run("--apply")


@patch("notifications.services.NotificationService.send_notification")
def test_cleanup_earliest_connection_keeps_number(send, company, other_company):
    _crm_account(company, "p3", days_old=1)
    inbox = _inbox_number(other_company, "p3", days_old=30)

    _run("--apply")

    assert WhatsAppAccount.objects.get(phone_number_id="p3").status == "disconnected"
    inbox.refresh_from_db()
    assert inbox.status == "connected"
    assert send.call_args.kwargs["data"]["error_key"] == "whatsapp_number_in_use_by_other_company"


@patch("notifications.services.NotificationService.send_notification")
def test_cleanup_keep_override(send, company, other_company):
    _crm_account(company, "p4", days_old=1)
    inbox = _inbox_number(other_company, "p4", days_old=30)

    _run("--apply", "--keep", "p4=crm")

    assert WhatsAppAccount.objects.get(phone_number_id="p4").status == "connected"
    inbox.refresh_from_db()
    assert inbox.status == "disconnected"


@patch("notifications.services.NotificationService.send_notification")
def test_cleanup_refuses_to_take_platform_number(send, company, platform_pid):
    wa = _crm_account(company, platform_pid)
    out = _run("--apply", "--keep", f"{platform_pid}=crm")
    assert "platform number" in out
    wa.refresh_from_db()
    assert wa.status == "connected"
    send.assert_not_called()


# --- connect endpoint reports the conflict to the user ------------------------------


def _embedded_complete(api_client, account, phone_number_id):
    handler = MagicMock()
    handler.exchange_code_for_token.return_value = {"access_token": "new-tok"}
    handler.get_user_info.return_value = {"id": "meta-user", "name": "Meta"}
    with patch("integrations.views.viewsets_accounts.get_oauth_handler", return_value=handler), patch(
        "integrations.services.token_lifecycle.upgrade_token_data_to_long_lived",
        side_effect=lambda platform, data: data,
    ):
        return api_client.post(
            f"/api/integrations/accounts/{account.id}/whatsapp/embedded-signup/complete/",
            {"code": "abc", "waba_id": "waba_x", "phone_number_id": phone_number_id},
            format="json",
        )


def test_embedded_signup_conflict_returns_error_key(authenticated_admin, company, other_company, wa_enabled):
    _crm_account(other_company, "someone_elses_pid")
    acc = IntegrationAccount.objects.create(
        company=company, platform="whatsapp", name="New WA", status="disconnected"
    )

    resp = _embedded_complete(authenticated_admin, acc, "someone_elses_pid")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "whatsapp_number_in_use_by_other_company"
    acc.refresh_from_db()
    assert acc.status == "error"
    assert WhatsAppAccount.objects.get(phone_number_id="someone_elses_pid").company_id == other_company.id


def test_embedded_signup_conflict_keeps_working_account_connected(
    authenticated_admin, company, other_company, wa_enabled
):
    mine = _crm_account(company, "my_working_pid")
    _crm_account(other_company, "someone_elses_pid")

    resp = _embedded_complete(authenticated_admin, mine.integration_account, "someone_elses_pid")

    assert resp.status_code == 400
    acc = mine.integration_account
    acc.refresh_from_db()
    assert acc.status == "connected"
    assert acc.get_access_token() != "new-tok"
    mine.refresh_from_db()
    assert mine.status == "connected"
