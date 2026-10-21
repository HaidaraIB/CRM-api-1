"""
Emergency calls (acceptance #10, spec §14; docs/TELEPHONY.md, Emergency calls).

Policy, decided 2026-10-17:

* every tenant's phones can dial the platform's emergency numbers
  (``settings.TELEPHONY_EMERGENCY_NUMBERS``). The compiler routes them out the
  tenant's own trunk, with a verified number as caller id, even when no
  outbound route allows short codes;
* they bypass every suspension: billing, a plan without telephony, a fraud
  block and a platform suspend. The compiled lines never check the node's
  outbound switch, and are compiled while ``outbound_suspended`` is set;
* no extension may use an emergency number;
* each emergency call alerts the company's admins once (``pbx_emergency_call``,
  Android channel ``system``, never muted), as soon as the node reports the call;
* emergency calls never count against the plan's monthly minutes. The node's
  fraud detector ignores them (it only watches ``+`` destinations), and a fraud
  block's hang-up never reaches them (they are not outbound channels on the node).

The carrier or the GSM gateway's SIM does the actual emergency routing and any
location handling. The node's translator reports a short destination such as
``112`` as an internal call (it looks like an extension), so the CRM records
an emergency call as outbound itself.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.utils import timezone

from ..models import PbxCall

logger = logging.getLogger(__name__)

NOTIFICATION_TYPE = "pbx_emergency_call"
_NUMBER_RE = re.compile(r"^[1-9][0-9]{1,5}$")  # short codes; the same shape the dialplan and events accept
FSI, PDI = chr(0x2068), chr(0x2069)


def numbers() -> list[str]:
    """The platform's emergency numbers, in order, without duplicates or malformed entries."""
    out: list[str] = []
    for raw in getattr(settings, "TELEPHONY_EMERGENCY_NUMBERS", []) or []:
        value = str(raw).strip()
        if not _NUMBER_RE.match(value):
            logger.warning("telephony: ignoring malformed emergency number %r", value)
            continue
        if value not in out:
            out.append(value)
    return out


def is_emergency(number: str) -> bool:
    return bool(number) and number in numbers()


def notify_emergency(call_id) -> int:
    """Alert the company's admins that someone dialled an emergency number; once per call."""
    from notifications.services import NotificationService

    from .alerts import recipients

    call = PbxCall.objects.select_related("extension__user").filter(pk=call_id).first()
    if call is None or not is_emergency(call.to_party):
        return 0
    if not PbxCall.objects.filter(pk=call.pk, emergency_notified_at__isnull=True).update(
        emergency_notified_at=timezone.now()
    ):
        return 0
    ext = call.extension
    who = ""
    if ext is not None:
        name = (ext.user.get_full_name() or ext.user.username) if ext.user_id else ext.display_name
        who = f"{name} ({FSI}{ext.number}{PDI})" if name else f"{FSI}{ext.number}{PDI}"
    elif call.from_party:
        who = f"{FSI}{call.from_party}{PDI}"
    data = {"call_id": str(call.pk), "number": f"{FSI}{call.to_party}{PDI}", "caller": who or "—"}
    sent = 0
    for user in recipients(call.company_id):
        try:
            # Never muted: no per-type switch and no quiet hours hold an emergency alert.
            NotificationService.send_notification(user, notification_type=NOTIFICATION_TYPE, data=dict(data),
                                                  skip_settings_check=True)
            sent += 1
        except Exception:  # a push failure never breaks event ingest
            logger.exception("telephony: emergency-call push failed call=%s user=%s", call.pk, user.pk)
    logger.warning("telephony: emergency call %s to %s in company %s (%d admins alerted)",
                   call.pk, call.to_party, call.company_id, sent)
    return sent
