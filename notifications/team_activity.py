import logging
from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from notifications.models import Notification, NotificationSettings, NotificationType
from notifications.services import NotificationService
from notifications.translations import get_team_activity_text, normalize_notification_language

logger = logging.getLogger(__name__)
User = get_user_model()

# Preference keys (synced with Flutter notification settings / camelCase variants).
_TEAM_ACTIVITY_ACTION_SETTINGS_KEY = "team_activity_action"
_TEAM_ACTIVITY_STATUS_SETTINGS_KEY = "team_activity_status"
_TEAM_ACTIVITY_OVERDUE_SETTINGS_KEY = "team_activity_overdue"


def team_activity_settings_key(action: Optional[str]) -> str:
    """
    Map team_activity action to a per-category preference key so owners can
    disable status / actions / overdue independently.
    """
    key = (action or "").strip().lower()
    if key == "status_change":
        return _TEAM_ACTIVITY_STATUS_SETTINGS_KEY
    if key == "no_follow_up":
        return _TEAM_ACTIVITY_OVERDUE_SETTINGS_KEY
    if key in {
        "call_logged",
        "visit_logged",
        "field_visit_logged",
        "task_created",
        "deal_won",
    }:
        return _TEAM_ACTIVITY_ACTION_SETTINGS_KEY
    # Unknown / legacy actions (edit, assignment) — treated as master team_activity
    return NotificationType.TEAM_ACTIVITY


def _owner_allows_team_activity(owner, action: str) -> bool:
    settings_obj = NotificationSettings.get_or_create_for_user(owner)
    if not settings_obj.enabled:
        return False
    pref = team_activity_settings_key(action)
    # Category key first, then legacy master `team_activity`, then default on.
    if settings_obj.notification_types:
        for candidate in (pref, NotificationType.TEAM_ACTIVITY):
            enabled = settings_obj.notification_types.get(candidate)
            if enabled is None:
                camel = settings_obj._snake_to_camel(candidate)
                enabled = settings_obj.notification_types.get(camel)
            if enabled is not None:
                return bool(enabled)
    return True


def notify_owner_team_activity(
    actor,
    company,
    *,
    action: str,
    **fields: Any,
) -> bool:
    """
    Notify company owner about a teammate activity (localized to owner's language).

    Always persists a Notification row (when allowed by settings), then attempts FCM
    without duplicating the DB row.

    Guardrails:
    - actor and company are required
    - skip self notifications (owner acting)
    - skip inactive owner
    - skip when owner disabled this team-activity category
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

    if not _owner_allows_team_activity(owner, action):
        logger.info(
            "Owner team activity skipped (settings) company=%s action=%s",
            getattr(company, "id", None),
            action,
        )
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
    # Ensure lead_id is always present for mobile deep-link (stringified by FCM layer).
    if "lead_id" in payload and payload["lead_id"] is not None:
        payload["lead_id"] = payload["lead_id"]

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
            skip_settings_check=True,  # already gated by category preference above
        )
    except Exception as exc:
        logger.error("Error sending owner team activity push: %s", exc)
        return False
