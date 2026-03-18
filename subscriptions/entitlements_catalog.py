"""
Canonical catalog of plan entitlements (features, quotas, usage limits).

This module intentionally contains only constants and helpers so it can be
imported from multiple apps without causing import cycles.
"""

from __future__ import annotations

# Feature flags (boolean)
FEATURE_KEYS = (
    # Integrations / messaging
    "whatsapp_enabled",
    "sms_enabled",
    # Platform/system modules
    "backups_enabled",
    "lead_import_enabled",
)

# Quotas (integer or "unlimited"/None)
QUOTA_KEYS = (
    "max_users",
    "max_clients",
    # Optional expansions (kept here for forward-compat; may not be enforced yet)
    "max_deals",
    "max_tasks",
    "max_integration_accounts",
    "max_whatsapp_numbers",
    "max_message_templates",
)

# Usage limits (monthly counters)
USAGE_KEYS_MONTHLY = (
    "monthly_sms_messages",
    "monthly_whatsapp_messages",
    "monthly_notifications",
)


DEFAULT_FEATURES = {
    # By default, keep current behavior: everything that exists stays enabled
    "whatsapp_enabled": True,
    "sms_enabled": True,
    "backups_enabled": True,
    "lead_import_enabled": True,
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

