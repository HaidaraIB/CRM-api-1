"""Inbox round-robin assignment and call-ring escalation."""

from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _agent(company, username):
    from accounts.models import User

    user = User.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        company=company,
        role="call_center",
    )
    user.last_seen_at = timezone.now()
    user.save(update_fields=["last_seen_at"])
    return user


def _conversation(company, external_id):
    from integrations.models import (
        SocialChannel,
        SocialContact,
        SocialConversation,
        WhatsAppAccount,
        WhatsAppPurpose,
    )

    number = WhatsAppAccount.objects.filter(
        company=company, purpose=WhatsAppPurpose.INBOX
    ).first()
    if number is None:
        number = WhatsAppAccount.objects.create(
            company=company,
            waba_id="waba",
            phone_number_id=f"inbox-{company.id}",
            purpose=WhatsAppPurpose.INBOX,
            status="connected",
        )
    contact = SocialContact.objects.create(
        company=company,
        channel=SocialChannel.WHATSAPP,
        external_id=external_id,
        wa_inbox_number=number,
        name=external_id,
    )
    return SocialConversation.objects.create(
        company=company,
        channel=SocialChannel.WHATSAPP,
        contact=contact,
        wa_inbox_number=number,
        status="open",
    )


def test_round_robin_splits_new_conversations(company):
    from integrations.services.inbox_assignment import assign_conversation

    a = _agent(company, "cc_a")
    b = _agent(company, "cc_b")
    first = assign_conversation(_conversation(company, "100"))
    second = assign_conversation(_conversation(company, "200"))
    assert {first.id, second.id} == {a.id, b.id}


def test_sticky_while_ready_then_requeue_when_away(company):
    from integrations.services.inbox_assignment import assign_conversation

    a = _agent(company, "cc_sticky")
    _agent(company, "cc_other")
    conv = _conversation(company, "300")
    assert assign_conversation(conv).id == assign_conversation(conv).id
    a.whatsapp_call_away_until = timezone.now() + timedelta(hours=1)
    a.save(update_fields=["whatsapp_call_away_until"])
    conv.refresh_from_db()
    nxt = assign_conversation(conv)
    assert nxt.id != a.id


def test_unassigned_when_nobody_ready(company):
    from integrations.services.inbox_assignment import assign_conversation

    conv = _conversation(company, "400")
    assert assign_conversation(conv) is None
    conv.refresh_from_db()
    assert conv.assigned_to_id is None


def test_drain_assigns_waiting_threads(company):
    from integrations.services.inbox_assignment import drain_unassigned

    _agent(company, "cc_drain")
    conv = _conversation(company, "500")
    assert drain_unassigned(company) == 1
    conv.refresh_from_db()
    assert conv.assigned_to_id is not None


def test_inbox_ring_escalates_after_20s(company):
    from integrations.models import WhatsAppCall, WhatsAppCallDirection, WhatsAppCallStatus
    from integrations.services.inbox_assignment import escalate_due_inbox_rings

    agent = _agent(company, "cc_ring")
    conv = _conversation(company, "600")
    conv.assigned_to = agent
    conv.save(update_fields=["assigned_to"])
    call = WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=conv.wa_inbox_number,
        social_conversation=conv,
        meta_call_id="ring-1",
        direction=WhatsAppCallDirection.INBOUND,
        status=WhatsAppCallStatus.RINGING,
        peer_phone="600",
    )
    WhatsAppCall.objects.filter(pk=call.pk).update(
        created_at=timezone.now() - timedelta(seconds=21)
    )
    assert escalate_due_inbox_rings(company.id) == 1
    call.refresh_from_db()
    assert call.ring_escalated_at is not None
    assert escalate_due_inbox_rings(company.id) == 0
