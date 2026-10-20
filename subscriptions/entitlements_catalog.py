"""
Canonical catalog of plan entitlements (features, quotas, usage limits).

This module intentionally contains only constants and helpers so it can be
imported from multiple apps without causing import cycles.
"""

from __future__ import annotations

# Feature flags (boolean)
FEATURE_KEYS = (
    # Per-plan integration inclusion toggles
    "integration_meta",
    "integration_tiktok",
    "integration_whatsapp",
    "integration_twilio",
    "integration_otpiq",
    "integration_openai",
    "integration_api",
    "integration_mujeb",
    "integration_meta_inbox",
    # Loop Cloud PBX (paid add-on)
    "telephony",
)

# Quotas (integer or "unlimited"/None)
QUOTA_KEYS = (
    "max_employees",
    # Legacy alias
    "max_users",
    "max_clients",
    "max_deals",
    "max_storage_bytes",
    "max_file_size_bytes",
    # Loop Cloud PBX
    "max_pbx_extensions",
    "max_pbx_trunks",
    "max_pbx_concurrent_calls",
    "max_pbx_cps",
    "max_pbx_recording_bytes",
)

# Usage limits (monthly counters)
USAGE_KEYS_MONTHLY = (
    "monthly_sms_messages",
    "monthly_whatsapp_messages",
    "monthly_notifications",
    "monthly_pbx_minutes",
)


DEFAULT_FEATURES = {
    # Keep default behavior permissive for existing plans.
    "integration_meta": True,
    "integration_tiktok": True,
    "integration_whatsapp": True,
    "integration_twilio": True,
    "integration_otpiq": True,
    "integration_openai": True,
    "integration_api": True,
    "integration_mujeb": True,
    "integration_meta_inbox": True,
    # Telephony is a paid add-on: off unless the plan turns it on.
    "telephony": False,
}

# Default usage limits: None means unlimited (keeps current behavior)
DEFAULT_USAGE_LIMITS_MONTHLY = {
    "monthly_sms_messages": None,
    "monthly_whatsapp_messages": None,
    "monthly_notifications": None,
    "monthly_pbx_minutes": None,
}

# Platform defaults when a plan omits these quota keys (unlike max_deals = unlimited).
# Explicit "unlimited" on the plan still means no cap.
DEFAULT_QUOTA_LIMITS = {
    "max_file_size_bytes": 20 * 1024 * 1024,  # 20 MB
    "max_storage_bytes": 1 * 1024 * 1024 * 1024,  # 1 GB
    # Loop Cloud PBX starting assumptions (spec section 1); plans override.
    "max_pbx_extensions": 10,
    "max_pbx_trunks": 2,
    "max_pbx_concurrent_calls": 5,
    "max_pbx_cps": 1,
    "max_pbx_recording_bytes": 5 * 1024 * 1024 * 1024,  # 5 GB
}


def normalize_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on", "enabled"):
            return True
        if v in ("0", "false", "no", "n", "off", "disabled"):
            return False
    return default

