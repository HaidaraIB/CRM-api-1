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
    if key in {"no_follow_up", "no_follow_up_digest"}:
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


def _eligible_supervisors(company, action: str, actor):
    """
    Supervisors of ``company`` whose owner-managed SupervisorPermission opts them
    into this team-activity category. Opt-in defaults to off (new capability).
    """
    from accounts.models import Role, SupervisorPermission

    category_key = team_activity_settings_key(action)
    actor_id = getattr(actor, "pk", None)
    supervisors = (
        User.objects.filter(company=company, role=Role.SUPERVISOR.value, is_active=True)
        .exclude(pk=actor_id)
        .select_related("supervisor_permissions")
    )
    eligible = []
    for supervisor in supervisors:
        permission: Optional["SupervisorPermission"] = getattr(
            supervisor, "supervisor_permissions", None
        )
        if permission and permission.allows_team_activity(category_key):
            eligible.append(supervisor)
    return eligible


def _send_team_activity_to_user(
    recipient,
    actor,
    action: str,
    fields: Dict[str, Any],
    sender_role,
) -> bool:
    """Persist + push a team-activity notification to one recipient, localized to them."""
    lang = normalize_notification_language(recipient.language)
    employee = (actor.get_full_name() or actor.username) if actor is not None else ""
    lead_display = (fields.get("lead") or fields.get("lead_name") or "").strip()

    text = get_team_activity_text(
        lang,
        action,
        employee=employee,
        lead=lead_display,
        **fields,
    )

    payload: Dict[str, Any] = {"action": action, **fields}
    if employee:
        payload["employee_name"] = employee
    if lead_display and "lead_name" not in payload:
        payload["lead_name"] = lead_display

    try:
        Notification.objects.create(
            user=recipient,
            type=NotificationType.TEAM_ACTIVITY,
            title=text["title"],
            body=text["body"],
            data=payload,
        )
    except Exception as exc:
        logger.error(
            "Error saving team activity notification for user=%s: %s", recipient.pk, exc
        )
        return False

    try:
        return NotificationService.send_notification(
            user=recipient,
            notification_type=NotificationType.TEAM_ACTIVITY,
            title=text["title"],
            body=text["body"],
            data=payload,
            sender_role=sender_role,
            language=lang,
            skip_database_insert=True,
            skip_settings_check=True,  # already gated by category preference above
        )
    except Exception as exc:
        logger.error("Error sending team activity push to user=%s: %s", recipient.pk, exc)
        return False


def notify_owner_team_activity(
    actor,
    company,
    *,
    action: str,
    **fields: Any,
) -> bool:
    """
    Notify the company owner, and any opted-in supervisors, about a teammate activity
    (each localized to the recipient's own language).

    Owner guardrails (unchanged):
    - company is required
    - skip self notifications (owner acting)
    - skip inactive owner
    - skip when owner disabled this team-activity category

    Supervisors are notified independently of the owner's settings: each supervisor
    only receives this category if the company owner explicitly enabled it for them
    (SupervisorPermission.notify_team_activity_*, opt-in / default off).

    ``actor`` may be None for aggregate notifications (e.g. the daily no-follow-up digest),
    which summarise many employees and therefore have no single acting user. In that case
    the self-notification guard does not apply.

    Returns whether the owner's notification was sent (supervisor fan-out is best-effort
    and does not affect the return value; existing callers ignore it either way).
    """
    if not company:
        return False

    sender_role = getattr(actor, "role", None) if actor is not None else None
    owner_id = getattr(company, "owner_id", None)
    owner_sent = False

    if owner_id and not (actor is not None and actor.pk == owner_id):
        owner = User.objects.filter(pk=owner_id).first()
        if owner is not None and getattr(owner, "is_active", False):
            if _owner_allows_team_activity(owner, action):
                owner_sent = _send_team_activity_to_user(
                    owner, actor, action, fields, sender_role
                )
            else:
                logger.info(
                    "Owner team activity skipped (settings) company=%s action=%s",
                    getattr(company, "id", None),
                    action,
                )

    for supervisor in _eligible_supervisors(company, action, actor):
        _send_team_activity_to_user(supervisor, actor, action, fields, sender_role)

    return owner_sent
