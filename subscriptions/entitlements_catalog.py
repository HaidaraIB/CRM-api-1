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
)

# Quotas (integer or "unlimited"/None)
QUOTA_KEYS = (
    "max_employees",
    # Legacy alias
    "max_users",
    "max_clients",
    "max_deals",
)

# Usage limits (monthly counters)
USAGE_KEYS_MONTHLY = (
    "monthly_sms_messages",
    "monthly_whatsapp_messages",
    "monthly_notifications",
)


DEFAULT_FEATURES = {
    # Keep default behavior permissive for existing plans.
    "integration_meta": True,
    "integration_tiktok": True,
    "integration_whatsapp": True,
    "integration_twilio": True,
}

# Default usage limits: None means unlimited (keeps current behavior)
DEFAULT_USAGE_LIMITS_MONTHLY = {
    "monthly_sms_messages": None,
    "monthly_whatsapp_messages": None,
    "monthly_notifications": None,
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

