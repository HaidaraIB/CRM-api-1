"""
Push notification for an inbound WhatsApp call that is ringing.

This was the one significant event in the product with no push path at all. Every
other thing a user waits on — a new lead, a walk-in arrival, a team-chat message,
a PBX screen pop — sends an FCM push. A ringing WhatsApp call was discovered only
by polling, which means a closed tab or a backgrounded phone never learned about
it, and the caller rang out.

Recipients mirror ``sync.counts.whatsapp_calls_pending_for_user`` exactly, because
that function decides who sees the call in the UI. If the two disagree, someone
gets a push for a call they cannot answer, or answers one they were never shown:

* an assigned lead rings only its assignee;
* an unassigned or unknown caller rings everyone eligible to pick it up;
* agents marked Away are never rung.

Delivery is best-effort and never raises into the webhook. Meta retries a webhook
we fail to acknowledge, and a retried call event would create duplicate rings —
so a push that cannot be sent must not take the webhook down with it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# An unassigned call goes to everyone eligible, which in a large tenant could be a
# lot of people. The UI only ever offers the first few rings anyway, and a
# hundred-way fan-out inside a webhook is its own problem.
MAX_RECIPIENTS = 25


def _eligible_recipients(call):
    """Users who should be rung for this call, in the UI's own order."""
    from django.contrib.auth import get_user_model

    from integrations.services.whatsapp_call_availability import (
        user_is_whatsapp_call_away,
    )
    from integrations.whatsapp_access import user_can_access_whatsapp_calls

    User = get_user_model()

    assigned_to_id = getattr(getattr(call, "client", None), "assigned_to_id", None)
    if assigned_to_id:
        candidates = User.objects.filter(pk=assigned_to_id, is_active=True)
    else:
        candidates = User.objects.filter(
            company_id=call.company_id, is_active=True
        ).order_by("id")[: MAX_RECIPIENTS * 2]

    recipients = []
    for user in candidates:
        try:
            if not user_can_access_whatsapp_calls(user):
                continue
            if user_is_whatsapp_call_away(user):
                continue
        except Exception:
            # A permission helper that errors must not silence the whole ring.
            continue
        recipients.append(user)
        if len(recipients) >= MAX_RECIPIENTS:
            break
    return recipients


def notify_inbound_ringing_call(call) -> int:
    """
    Push "incoming call" to whoever can answer it. Returns how many were notified.

    Never raises.
    """
    try:
        from notifications.models import NotificationType
        from notifications.services import NotificationService

        recipients = _eligible_recipients(call)
        if not recipients:
            return 0

        phone = call.peer_phone or ""
        client = getattr(call, "client", None)
        data = {
            "type": NotificationType.WHATSAPP_CALL_INCOMING,
            "call_id": str(call.id),
            "phone": phone,
            "phone_number": phone,
            "client_id": str(client.id) if client else "",
            "lead_id": str(client.id) if client else "",
            "lead_name": getattr(client, "name", "") or "",
            # Same vocabulary the other push senders use, so the clients' existing
            # invalidation buses route it without new mapping code.
            "invalidate": "whatsapp:calls",
        }

        sent = 0
        for user in recipients:
            try:
                NotificationService.send_notification(
                    user=user,
                    notification_type=NotificationType.WHATSAPP_CALL_INCOMING,
                    data=data,
                    # No inbox row. A ring is transient — it is answered, missed or
                    # rejected within seconds, and a bell entry for it would be
                    # stale before it was read. The call History tab is the record.
                    skip_database_insert=True,
                )
                sent += 1
            except Exception:
                logger.exception(
                    "Ringing-call push failed call=%s user=%s", call.id, user.id
                )
        return sent
    except Exception:
        logger.exception("Ringing-call push failed for call=%s", getattr(call, "id", None))
        return 0
