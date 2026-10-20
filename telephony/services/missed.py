"""
Missed-call push (Phase 6): tell the people a caller was trying to reach.

Runs once per call, when it first turns terminal with a known direction:
``PbxCall.missed_notified_at`` is claimed atomically, so replays and a late
``call.created`` never notify twice. An inbound call counts as missed when it ended ``missed``,
``no_answer`` or ``abandoned`` (the same set as the usage rollup).

Recipients, without duplicates and active users only:

1. the user of the extension the call was placed to;
2. otherwise the users whose phones rang (the call's extension legs);
3. otherwise the members of the queue it waited in (no member was rung);
4. otherwise the lead's assignee (a caller who hung up in the IVR, for example).

The push is ``pbx_call_missed`` (Android channel ``tasks``, like a call reminder:
it asks for a call back). Its data carries ``call_id`` and, when the caller is a
lead, ``client_id``, so the app opens the lead or the call history.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from ..models import PbxCall, PbxQueueMember

logger = logging.getLogger(__name__)

NOTIFICATION_TYPE = "pbx_call_missed"
FSI, PDI = chr(0x2068), chr(0x2069)  # Unicode first-strong isolate / pop directional isolate
MISSED_STATUSES = frozenset({PbxCall.Status.MISSED, PbxCall.Status.NO_ANSWER, PbxCall.Status.ABANDONED})


def is_missed(call: PbxCall) -> bool:
    return call.direction == PbxCall.Direction.INBOUND and call.status in MISSED_STATUSES and call.answered_at is None


def recipients(call: PbxCall) -> list:
    users = []
    if call.extension_id and call.extension.user_id:
        users = [call.extension.user]
    if not users:
        # The agents whose phones rang (queue members ring through Local channels,
        # which are not legs, so these are the real agent devices).
        legs = call.legs.filter(extension__user__isnull=False).select_related("extension__user").order_by("started_at")
        users = [leg.extension.user for leg in legs]
    if not users and call.queue_id:
        members = (
            PbxQueueMember.objects.filter(queue_id=call.queue_id, extension__user__isnull=False)
            .select_related("extension__user")
            .order_by("penalty", "extension__number")
        )
        users = [m.extension.user for m in members]
    if not users and call.crm_contact_id and call.crm_contact.assigned_to_id:
        users = [call.crm_contact.assigned_to]
    seen, out = set(), []
    for user in users:
        if user.pk in seen or not user.is_active or user.company_id != call.company_id:
            continue
        seen.add(user.pk)
        out.append(user)
    return out


def notify_missed(call_id) -> int:
    """Push the missed call to its recipients; returns how many users were notified."""
    from notifications.services import NotificationService

    call = (
        PbxCall.objects.select_related("extension__user", "crm_contact__assigned_to")
        .filter(pk=call_id)
        .first()
    )
    if call is None or not is_missed(call):
        return 0
    # Claim the push: a late call.created re-adds a terminal call to ended_calls.
    if not PbxCall.objects.filter(pk=call.pk, missed_notified_at__isnull=True).update(missed_notified_at=timezone.now()):
        return 0
    caller = call.from_party if call.from_party and call.from_party != "anonymous" else ""
    lead_name = call.crm_contact.name if call.crm_contact_id else ""
    data = {
        "call_id": str(call.pk),
        "phone": caller,
        # The translated body reads "Missed call from {caller}": the lead's name, else the number,
        # isolated left-to-right (FSI…PDI) so an Arabic body keeps its leading "+" in place.
        "caller": lead_name or (f"{FSI}{caller}{PDI}" if caller else "—"),
        # An open Phone calls screen (mobile) re-reads on this, like the digest's telephony slice.
        "invalidate": "telephony:calls",
    }
    if call.crm_contact_id:
        data["client_id"] = str(call.crm_contact_id)
    sent = 0
    for user in recipients(call):
        try:
            NotificationService.send_notification(user, notification_type=NOTIFICATION_TYPE, data=dict(data))
            sent += 1
        except Exception:  # a push failure never breaks event ingest
            logger.exception("telephony: missed-call push failed call=%s user=%s", call.pk, user.pk)
    return sent
