from __future__ import annotations

from typing import Any

from integrations.models import IntegrationAccount, TwilioSettings, WhatsAppAccount
from subscriptions.entitlements import build_company_entitlements

INTEGRATION_POLICY_PLATFORMS = ("meta", "tiktok", "whatsapp", "twilio")
PLAN_INTEGRATION_FEATURE_MAP = {
    "meta": "integration_meta",
    "tiktok": "integration_tiktok",
    "whatsapp": "integration_whatsapp",
    "twilio": "integration_twilio",
}
INTEGRATION_POLICY_DEFAULTS = {
    "global_enabled": True,
    "global_message": "",
    "company_overrides": {},
}


def _get_platform_policy(policies: dict[str, Any] | None, platform: str) -> dict[str, Any]:
    raw = (policies or {}).get(platform) or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "global_enabled": bool(raw.get("global_enabled", INTEGRATION_POLICY_DEFAULTS["global_enabled"])),
        "global_message": (raw.get("global_message") or "").strip(),
        "company_overrides": raw.get("company_overrides") if isinstance(raw.get("company_overrides"), dict) else {},
    }


def get_effective_integration_policy(policies: dict[str, Any] | None, *, company_id: int, platform: str) -> dict[str, Any]:
    policy = _get_platform_policy(policies, platform)
    global_enabled = policy["global_enabled"]
    global_message = policy["global_message"]
    override = policy["company_overrides"].get(str(company_id)) or {}
    override_enabled = bool(override.get("enabled", True))
    override_message = (override.get("message") or "").strip()

    if not global_enabled:
        return {
            "enabled": False,
            "message": global_message or "This integration is currently disabled by the administrator.",
            "scope": "global",
        }
    if not override_enabled:
        return {
            "enabled": False,
            "message": override_message or "This integration is currently disabled for your company.",
            "scope": "company",
        }
    return {"enabled": True, "message": "", "scope": "enabled"}


def get_plan_integration_access(company, platform: str) -> dict[str, Any]:
    feature_key = PLAN_INTEGRATION_FEATURE_MAP.get(platform)
    if not feature_key:
        return {"enabled": True, "message": "", "scope": "enabled", "feature_key": None}
    ent = build_company_entitlements(company)
    is_enabled = bool((ent.features or {}).get(feature_key, True))
    if is_enabled:
        return {"enabled": True, "message": "", "scope": "enabled", "feature_key": feature_key}
    return {
        "enabled": False,
        "message": "This integration is not included in your current plan.",
        "scope": "plan",
        "feature_key": feature_key,
    }


def apply_integration_policy_side_effects(*, previous_policies: dict[str, Any] | None, new_policies: dict[str, Any] | None) -> None:
    previous_policies = previous_policies or {}
    new_policies = new_policies or {}

    for platform in INTEGRATION_POLICY_PLATFORMS:
        prev = _get_platform_policy(previous_policies, platform)
        curr = _get_platform_policy(new_policies, platform)

        # Global disable transition
        if prev["global_enabled"] and not curr["global_enabled"]:
            IntegrationAccount.objects.filter(platform=platform, is_active=True).update(is_active=False)
            if platform == "whatsapp":
                WhatsAppAccount.objects.filter(status="connected").update(status="disconnected")
            if platform == "twilio":
                TwilioSettings.objects.filter(is_enabled=True).update(is_enabled=False)

        # Company-level disable transitions
        prev_overrides = prev["company_overrides"]
        curr_overrides = curr["company_overrides"]
        for company_id, company_policy in curr_overrides.items():
            prev_enabled = bool((prev_overrides.get(company_id) or {}).get("enabled", True))
            curr_enabled = bool((company_policy or {}).get("enabled", True))
            if prev_enabled and not curr_enabled:
                IntegrationAccount.objects.filter(
                    company_id=company_id,
                    platform=platform,
                    is_active=True,
                ).update(is_active=False)
                if platform == "whatsapp":
                    WhatsAppAccount.objects.filter(company_id=company_id, status="connected").update(status="disconnected")
                if platform == "twilio":
                    TwilioSettings.objects.filter(company_id=company_id, is_enabled=True).update(is_enabled=False)
