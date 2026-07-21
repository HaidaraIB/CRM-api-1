"""
Map notification_type (DB / FCM data `type`) to Android notification channel id.

Must stay in sync with crm_mobile `NotificationService._getChannelForType`
channel ids: general, leads, deals, tasks, reminders, whatsapp, campaigns,
reports, system, tenant_chat, team_activity.

For `team_activity`, the owner feed reuses the sound/channel of the related
category based on `data.action` (e.g. call_logged -> tasks, deal_won -> deals).
"""

from __future__ import annotations

from typing import Any, Optional


# Owner team-activity actions -> existing category channel (and its custom sound).
_TEAM_ACTIVITY_ACTION_CHANNELS: dict[str, str] = {
    # Lead lifecycle / edits
    "status_change": "leads",
    "assignment": "leads",
    "edit": "leads",
    "lead_created": "leads",
    "no_follow_up": "leads",
    # Employee actions on leads
    "call_logged": "tasks",
    "visit_logged": "tasks",
    "field_visit_logged": "tasks",
    "task_created": "tasks",
    # Deals
    "deal_won": "deals",
}


def team_activity_channel_for_action(action: Optional[str]) -> str:
    """Map team_activity action to a category channel that already has a custom sound."""
    key = (action or "").strip().lower()
    return _TEAM_ACTIVITY_ACTION_CHANNELS.get(key, "team_activity")


def android_notification_raw_sound_basename(
    notification_type: str,
    *,
    action: Optional[str] = None,
) -> str | None:
    """
    Basename (no extension) of res/raw sound for this type, or None for default.

    Matches crm_mobile naming: notif_<channel_id>.wav (e.g. reports -> notif_reports).
    """
    cid = android_notification_channel_id(notification_type, action=action)
    if cid == "general":
        return None
    return f"notif_{cid}"


def ios_notification_sound_filename(
    notification_type: str,
    *,
    action: Optional[str] = None,
) -> str | None:
    """
    Filename (with extension) of a bundled iOS sound for this type, or None for default.

    Must match crm_mobile `NotificationService._iosSoundFileForChannelId` and files under
    ios/Runner/*.wav included in the Xcode Copy Bundle Resources phase.
    """
    base = android_notification_raw_sound_basename(notification_type, action=action)
    if base is None:
        return None
    return f"{base}.wav"


def tenant_chat_ios_sound_filename() -> str:
    """Bundled iOS sound for team chat pushes (ios/Runner/notif_tenant_chat.wav)."""
    return "notif_tenant_chat.wav"


def tenant_chat_apns_collapse_id(conversation_id: str | int | None) -> str | None:
    """APNs collapse id so iOS replaces the previous banner for the same conversation."""
    if conversation_id is None:
        return None
    cid = str(conversation_id).strip()
    if not cid:
        return None
    return f"tenant_chat_{cid}"[:64]


def android_notification_channel_id(
    notification_type: str,
    *,
    action: Optional[str] = None,
) -> str:
    """
    Return the Android channel_id to attach to FCM so the system tray uses
    the same channel (and custom sound) as flutter_local_notifications.

    For team_activity, pass the payload `action` so each activity reuses its
    category sound (leads / tasks / deals).
    """
    t = (notification_type or "general").strip()
    if not t:
        return "general"

    # --- Leads (core) ---
    if t in {
        "new_lead",
        "lead_no_follow_up",
        "lead_reengaged",
        "lead_contact_failed",
        "lead_status_changed",
        "lead_assigned",
        "lead_transferred",
        "lead_updated",
        "lead_reminder",
    }:
        return "leads"

    # --- Owner team feed: reuse category sounds by action ---
    if t == "team_activity":
        return team_activity_channel_for_action(action)

    # --- WhatsApp ---
    if t in {
        "whatsapp_message_received",
        "whatsapp_template_sent",
        "whatsapp_send_failed",
        "whatsapp_waiting_response",
    }:
        return "whatsapp"

    # --- Campaigns ---
    if t in {
        "campaign_performance",
        "campaign_low_performance",
        "campaign_stopped",
        "campaign_budget_alert",
    }:
        return "campaigns"

    # --- Deals ---
    if t in {
        "deal_created",
        "deal_updated",
        "deal_closed",
        "deal_reminder",
    }:
        return "deals"

    # --- Tasks & time-based (mobile maps these to `tasks`) ---
    if t in {
        "task_created",
        "task_completed",
        "task_reminder",
        "call_reminder",
        "visit_reminder",
        "reception_visit_reminder",
        "field_visit_reminder",
        "reception_field_visit_reminder",
    }:
        return "tasks"

    # --- Reports ---
    if t in {
        "daily_report",
        "weekly_report",
        "top_employee",
    }:
        return "reports"

    # --- System & subscription ---
    if t in {
        "login_from_new_device",
        "system_update",
        "subscription_expiring",
        "payment_failed",
        "subscription_expired",
    }:
        return "system"

    # --- General / unknown / types without dedicated sound on mobile ---
    if t in {
        "general",
        "broadcast",
    }:
        return "general"

    return "general"


def resolve_notification_sound_channel(
    notification_type: str,
    data: Optional[dict[str, Any]] = None,
) -> str:
    """Convenience: channel id from type + optional FCM data payload."""
    action = None
    if data:
        action = data.get("action")
        if action is not None:
            action = str(action)
    return android_notification_channel_id(notification_type, action=action)
