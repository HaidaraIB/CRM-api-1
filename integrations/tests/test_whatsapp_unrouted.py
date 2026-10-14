"""Held inbound WhatsApp messages are replayed once the number is connected."""

import json

import pytest
from django.core.management import call_command
from django.utils import timezone

from integrations.models import (
    IntegrationAccount,
    IntegrationPlatform,
    SocialMessage,
    WhatsAppAccount,
    WhatsAppPurpose,
    WhatsAppUnroutedMessage,
    WhatsAppUnroutedReason,
)
from integrations.services.whatsapp_unrouted import (
    import_webhook_payload,
    iter_webhook_payloads_from_log,
    replay_unrouted,
)
from integrations.whatsapp_webhook import process_whatsapp_message


def _inbox_row(company, *, status="disconnected", phone_number_id="inbox-held-1"):
    row = WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba-held",
        phone_number_id=phone_number_id,
        display_phone_number="9647700004444",
        status=status,
        purpose=WhatsAppPurpose.INBOX,
        integration_account=None,
    )
    row.set_access_token("held-token")
    row.save(update_fields=["access_token"])
    return row


def _inbound(wamid, body="hello"):
    return {
        "from": "9647709990001",
        "id": wamid,
        "timestamp": str(int(timezone.now().timestamp())),
        "type": "text",
        "text": {"body": body},
    }


@pytest.mark.django_db
def test_disconnected_inbox_holds_then_replays_once(company):
    row = _inbox_row(company)
    process_whatsapp_message(_inbound("wamid.held.1"), row.phone_number_id)
    assert SocialMessage.objects.filter(external_message_id="wamid.held.1").count() == 0
    held = WhatsAppUnroutedMessage.objects.get(external_message_id="wamid.held.1")
    assert held.reason == WhatsAppUnroutedReason.NO_OWNER
    assert held.company_id == company.id
    assert held.replayed_at is None

    row.status = "connected"
    row.save(update_fields=["status"])

    first = replay_unrouted(row.phone_number_id)
    assert first["replayed"] == 1
    assert SocialMessage.objects.filter(external_message_id="wamid.held.1").count() == 1
    held.refresh_from_db()
    assert held.replayed_at is not None

    second = replay_unrouted(row.phone_number_id)
    assert second["replayed"] == 0
    assert SocialMessage.objects.filter(external_message_id="wamid.held.1").count() == 1


@pytest.mark.django_db
def test_duplicate_webhook_while_disconnected_keeps_one_row(company):
    row = _inbox_row(company, phone_number_id="inbox-held-2")
    process_whatsapp_message(_inbound("wamid.held.2"), row.phone_number_id)
    process_whatsapp_message(_inbound("wamid.held.2"), row.phone_number_id)
    assert WhatsAppUnroutedMessage.objects.filter(external_message_id="wamid.held.2").count() == 1


def test_log_payload_parser_reads_debug_block():
    payload = {
        "entry": [
            {
                "id": "waba",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "123"},
                            "messages": [{"from": "1", "id": "wamid.log", "type": "text", "text": {"body": "hi"}}],
                        },
                    }
                ],
            }
        ]
    }
    text = "INFO 2026-10-14 11:56:44,873 whatsapp_webhook WhatsApp webhook payload: " + json.dumps(payload, indent=2)
    found = list(iter_webhook_payloads_from_log(text))
    assert len(found) == 1
    assert import_webhook_payload(found[0], apply=False) == 1


@pytest.mark.django_db
def test_repair_command_restores_inbox_number_matching_connected_integration(company):
    account = IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.WHATSAPP_INBOX,
        name="Inbox still connected",
        status="connected",
        metadata={"phone_number_id": "inbox-repair-1", "waba_id": "waba-repair"},
    )
    account.set_access_token("integration-token")
    account.save(update_fields=["access_token"])
    row = _inbox_row(company, phone_number_id="inbox-repair-1")
    row.set_access_token(None)
    row.save(update_fields=["access_token"])

    call_command("repair_whatsapp_inbox_numbers", "--apply")
    row.refresh_from_db()
    assert row.status == "connected"
    assert row.integration_account_id == account.id
    assert row.get_access_token() == "integration-token"
    assert row.error_message in (None, "")
