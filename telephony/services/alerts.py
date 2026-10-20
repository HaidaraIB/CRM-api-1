"""
Trunk and gateway alerts (spec §12: "gateway WAN/VPN loss shows degraded status and alerts").

When a node reports a trunk going ``down``, or coming back from ``down``
(``gateway.status_changed``, contract section 5), the company's admins get a push:
``pbx_trunk_down`` / ``pbx_trunk_recovered`` (Android channel ``system``). A
trunk's first report (health still empty, e.g. a gateway being set up) never
alerts. The node only reports changes, so a flapping link alerts once per change.
"""

from __future__ import annotations

import logging

from ..models import PbxTrunk

logger = logging.getLogger(__name__)

DOWN, RECOVERED = "pbx_trunk_down", "pbx_trunk_recovered"


def recipients(company_id: int) -> list:
    """Active company admins (the people who can fix a trunk in Settings → Phone System)."""
    from accounts.models import Role, User

    return list(User.objects.filter(company_id=company_id, is_active=True, role=Role.ADMIN.value).order_by("pk"))


def notify_trunk_health(trunk_id, old: str, new: str) -> int:
    from notifications.services import NotificationService

    trunk = PbxTrunk.objects.filter(pk=trunk_id).first()
    if trunk is None or not old or old == new or "down" not in (old, new):
        return 0
    kind = DOWN if new == "down" else RECOVERED
    data = {"trunk_id": str(trunk.pk), "trunk_name": trunk.name, "trunk_kind": trunk.kind, "health": new}
    sent = 0
    for user in recipients(trunk.company_id):
        try:
            NotificationService.send_notification(user, notification_type=kind, data=dict(data))
            sent += 1
        except Exception:
            logger.exception("telephony: %s push failed trunk=%s user=%s", kind, trunk.pk, user.pk)
    return sent
