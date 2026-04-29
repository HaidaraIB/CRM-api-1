import logging
from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from notifications.models import Notification, NotificationType
from notifications.services import NotificationService
from notifications.translations import get_team_activity_text, normalize_notification_language

logger = logging.getLogger(__name__)
User = get_user_model()


def notify_owner_team_activity(
    actor,
    company,
    *,
    action: str,
    **fields: Any,
) -> bool:
    """
    Notify company owner about a teammate activity (localized to owner's language).

    Always persists a Notification row, then attempts FCM (respecting settings) without
    duplicating the DB row.

    Guardrails:
    - actor and company are required
    - skip self notifications (owner acting)
    - skip inactive owner
    """
    if not actor or not company:
        return False

    owner_id = getattr(company, "owner_id", None)
    if not owner_id or actor.pk == owner_id:
        return False

    try:
        owner = User.objects.get(pk=owner_id)
    except User.DoesNotExist:
        return False

    if not getattr(owner, "is_active", False):
        return False

    lang = normalize_notification_language(owner.language)
    employee = actor.get_full_name() or actor.username
    lead_display = (fields.get("lead") or fields.get("lead_name") or "").strip()

    text = get_team_activity_text(
        lang,
        action,
        employee=employee,
        lead=lead_display,
        **fields,
    )

    payload: Dict[str, Any] = {"action": action, "employee_name": employee, **fields}
    if lead_display and "lead_name" not in payload:
        payload["lead_name"] = lead_display

    try:
        Notification.objects.create(
            user=owner,
            type=NotificationType.TEAM_ACTIVITY,
            title=text["title"],
            body=text["body"],
            data=payload,
        )
    except Exception as exc:
        logger.error("Error saving owner team activity notification: %s", exc)
        return False

    try:
        return NotificationService.send_notification(
            user=owner,
            notification_type=NotificationType.TEAM_ACTIVITY,
            title=text["title"],
            body=text["body"],
            data=payload,
            sender_role=getattr(actor, "role", None),
            language=lang,
            skip_database_insert=True,
        )
    except Exception as exc:
        logger.error("Error sending owner team activity push: %s", exc)
        return False
