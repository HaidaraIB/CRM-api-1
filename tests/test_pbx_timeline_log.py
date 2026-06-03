"""PBX timeline: one ClientCall per linkedid."""

import pytest
from django.utils import timezone

from accounts.models import User
from companies.models import Company
from crm.models import Client, ClientCall, ClientCallSource
from integrations.models import (
    PbxCallDisposition,
    PbxCallDirection,
    PbxCallRecord,
    PbxEventType,
    PbxSettings,
)
from integrations.services.pbx_handler import _auto_log_client_call


@pytest.mark.django_db
def test_auto_log_dedupes_by_linkedid():
    user = User.objects.create_user(
        username="pbx_agent",
        email="pbx@example.com",
        password="test-pass-123",
    )
    company = Company.objects.create(
        name="PBX Co",
        domain="pbx-co-dedupe.example.com",
        owner=user,
    )
    user.company = company
    user.save(update_fields=["company"])
    client = Client.objects.create(
        company=company, name="Test Lead", phone_number="+9647812113063"
    )
    settings = PbxSettings.objects.create(
        company=company, is_enabled=True, auto_log_calls=True
    )
    linked = "1700000000.123"

    inbound = PbxCallRecord.objects.create(
        company=company,
        uniqueid="1700000000.1",
        linkedid=linked,
        event_type=PbxEventType.HANGUP,
        direction=PbxCallDirection.INBOUND,
        disposition=PbxCallDisposition.ANSWERED,
        billsec=57,
        client=client,
        agent=user,
        ended_at=timezone.now(),
    )
    outbound = PbxCallRecord.objects.create(
        company=company,
        uniqueid="1700000000.2",
        linkedid=linked,
        event_type=PbxEventType.HANGUP,
        direction=PbxCallDirection.OUTBOUND,
        disposition=PbxCallDisposition.UNKNOWN,
        billsec=0,
        client=client,
        agent=user,
        ended_at=timezone.now(),
    )

    first = _auto_log_client_call(settings, inbound, client, user)
    second = _auto_log_client_call(settings, outbound, client, user)

    assert first is not None
    assert second.id == first.id
    assert ClientCall.objects.filter(client=client, source=ClientCallSource.PBX).count() == 1
    assert first.pbx_call_record_id == inbound.id
    assert first.notes == "57s"
