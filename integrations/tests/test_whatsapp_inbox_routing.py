"""WhatsApp webhook routes inbox phone_number_id to SocialConversation, not CRM leads."""

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture
def whatsapp_inbox_number(company, db):
    from integrations.models import IntegrationAccount, IntegrationPlatform, WhatsAppInboxNumber

    account = IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.WHATSAPP_INBOX,
        name='Inbox WA',
        status='connected',
    )
    row = WhatsAppInboxNumber.objects.create(
        company=company,
        integration_account=account,
        waba_id='waba_inbox_1',
        phone_number_id='inbox_phone_99',
        display_phone_number='+9647701111111',
        status='connected',
    )
    row.set_access_token('test-token')
    row.save()
    return row


def test_inbox_webhook_does_not_create_client(whatsapp_inbox_number, company):
    from crm.models import Client
    from integrations.models import SocialConversation
    from integrations.whatsapp_webhook import process_whatsapp_message

    process_whatsapp_message(
        {
            'from': '9647709999999',
            'id': 'wamid.inbox.test.1',
            'timestamp': str(int(timezone.now().timestamp())),
            'type': 'text',
            'text': {'body': 'Hello inbox'},
        },
        whatsapp_inbox_number.phone_number_id,
    )
    assert Client.objects.filter(company=company).count() == 0
    assert SocialConversation.objects.filter(company=company, channel='whatsapp').exists()


def test_crm_and_inbox_numbers_must_differ(company, whatsapp_inbox_number, db):
    from integrations.models import IntegrationAccount, IntegrationPlatform, WhatsAppAccount
    from integrations.services.whatsapp_inbox_numbers import InboxNumberConflictError
    from integrations.services.whatsapp_inbox_numbers import upsert_inbox_number_from_embedded_signup

    wa_acc = IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.WHATSAPP,
        name='CRM WA',
        status='connected',
    )
    WhatsAppAccount.objects.create(
        company=company,
        integration_account=wa_acc,
        waba_id='waba_crm',
        phone_number_id='shared_pid',
        status='connected',
    )
    inbox_acc = IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.WHATSAPP_INBOX,
        name='Inbox',
        status='connected',
    )
    with pytest.raises(InboxNumberConflictError) as exc:
        upsert_inbox_number_from_embedded_signup(
            inbox_acc,
            'tok',
            waba_id='waba2',
            phone_number_id='shared_pid',
        )
    assert exc.value.error_key == 'whatsapp_inbox_number_in_use_by_crm'
