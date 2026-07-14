"""
Map notification_type (DB / FCM data `type`) to Android notification channel id.

Must stay in sync with crm_mobile `NotificationService._getChannelForType`
channel ids: general, leads, deals, tasks, reminders, whatsapp, campaigns,
reports, system, tenant_chat.
"""

from __future__ import annotations


def android_notification_raw_sound_basename(notification_type: str) -> str | None:
    """
    Basename (no extension) of res/raw sound for this type, or None for default.

    Matches crm_mobile naming: notif_<channel_id>.wav (e.g. reports -> notif_reports).
    """
    cid = android_notification_channel_id(notification_type)
    if cid == "general":
        return None
    return f"notif_{cid}"


def ios_notification_sound_filename(notification_type: str) -> str | None:
    """
    Filename (with extension) of a bundled iOS sound for this type, or None for default.

    Must match crm_mobile `NotificationService._iosSoundFileForChannelId` and files under
    ios/Runner/*.wav included in the Xcode Copy Bundle Resources phase.
    """
    base = android_notification_raw_sound_basename(notification_type)
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


def android_notification_channel_id(notification_type: str) -> str:
    """
    Return the Android channel_id to attach to FCM so the system tray uses
    the same channel (and custom sound) as flutter_local_notifications.
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
        "team_activity",
    }:
        return "general"

    return "general"
