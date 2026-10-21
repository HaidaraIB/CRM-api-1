"""
PBX call → CRM lead timeline.

When a call ends, an external call (inbound/outbound) is recorded on the lead as
a ``ClientCall`` with ``source="pbx"`` and a OneToOne link to the ``PbxCall`` —
the same shape WhatsApp calls use, so the existing timeline/activities merge and
the ``ClientCall`` signal (last-contacted, follow-up completion) apply unchanged.

The lead is the CRM contact the call was placed for (click-to-call), otherwise a
phone match on the remote party within the tenant's company. Internal calls and
calls with no matching lead are not linked. Idempotent: a call links at most once.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..models import PbxCall

DIRECTION_LABEL = {"inbound": "Incoming", "outbound": "Outgoing"}


def _remote_party(call: PbxCall) -> str:
    return call.from_party if call.direction == PbxCall.Direction.INBOUND else call.to_party


def _agent_user(call: PbxCall):
    for ext in (call.answered_by, call.extension):
        if ext is not None and ext.user_id:
            return ext.user
    return None


def link_call_to_crm(call_id):
    from crm.models import ClientCall, ClientCallSource
    from integrations.services.phone_match import find_client_by_phone

    with transaction.atomic():
        call = (
            PbxCall.objects.select_for_update()
            .select_related("answered_by__user", "extension__user", "crm_contact", "company")
            .filter(pk=call_id)
            .first()
        )
        if call is None or not call.is_terminal or call.direction not in DIRECTION_LABEL:
            return None
        if ClientCall.objects.filter(pbx_call=call).exists():
            return None

        agent = _agent_user(call)
        remote = _remote_party(call)
        client = call.crm_contact
        if client is None and remote and remote != "anonymous":
            client = find_client_by_phone(call.company, remote, prefer_assigned_to=agent)
        if client is None or client.company_id != call.company_id:
            return None
        if call.crm_contact_id is None:
            call.crm_contact = client
            call.save(update_fields=["crm_contact", "updated_at"])

        notes = f"Phone call · {DIRECTION_LABEL[call.direction]} · {call.get_status_display()}"
        if call.billsec_s:
            notes += f" · {call.billsec_s}s"
        return ClientCall.objects.create(
            client=client,
            source=ClientCallSource.PBX,
            pbx_call=call,
            notes=notes,
            call_datetime=call.answered_at or call.started_at or call.ended_at or timezone.now(),
            created_by=agent,
            dialed_phone_number=(remote or "")[:32],
            recording_status="ready" if call.recordings.exists() else ("pending" if call.recorded else ""),
            recording_duration_sec=call.billsec_s or None,
        )
