from __future__ import annotations

from typing import Any

from settings.models import SystemSettings

FEATURE_POLICY_KEYS = ("field_visit",)
FIELD_VISIT_FEATURE = "field_visit"
FEATURE_POLICY_DEFAULTS = {
    "global_enabled": True,
    "global_message": "",
    "company_overrides": {},
}


def _is_global_exception(company_policy: dict[str, Any] | None) -> bool:
    return isinstance(company_policy, dict) and company_policy.get("enabled") is True


def _get_feature_policy(policies: dict[str, Any] | None, feature_key: str) -> dict[str, Any]:
    raw = (policies or {}).get(feature_key) or {}
    if not isinstance(raw, dict):
        raw = {}
    company_overrides_raw = raw.get("company_overrides") or {}
    company_overrides = company_overrides_raw if isinstance(company_overrides_raw, dict) else {}
    return {
        "global_enabled": bool(raw.get("global_enabled", FEATURE_POLICY_DEFAULTS["global_enabled"])),
        "global_message": (raw.get("global_message") or "").strip(),
        "company_overrides": company_overrides,
    }


def normalize_feature_policies(value: object) -> dict[str, dict[str, Any]]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("feature_policies must be a JSON object.")
    normalized: dict[str, dict[str, Any]] = {}
    for feature_key in FEATURE_POLICY_KEYS:
        raw = value.get(feature_key) or {}
        if not isinstance(raw, dict):
            raw = {}
        company_overrides_raw = raw.get("company_overrides") or {}
        company_overrides: dict[str, dict[str, Any]] = {}
        if isinstance(company_overrides_raw, dict):
            for company_id, company_policy in company_overrides_raw.items():
                if not isinstance(company_policy, dict):
                    continue
                company_overrides[str(company_id)] = {
                    "enabled": bool(company_policy.get("enabled", True)),
                    "message": (company_policy.get("message") or "").strip(),
                }
        normalized[feature_key] = {
            "global_enabled": bool(raw.get("global_enabled", FEATURE_POLICY_DEFAULTS["global_enabled"])),
            "global_message": (raw.get("global_message") or "").strip(),
            "company_overrides": company_overrides,
        }
    return normalized


def get_effective_feature_policy(
    policies: dict[str, Any] | None,
    *,
    company_id: int,
    feature_key: str,
) -> dict[str, Any]:
    policy = _get_feature_policy(policies, feature_key)
    global_enabled = policy["global_enabled"]
    global_message = policy["global_message"]
    override = policy["company_overrides"].get(str(company_id)) or {}
    override_enabled = bool(override.get("enabled", True))
    override_message = (override.get("message") or "").strip()

    if not global_enabled:
        if _is_global_exception(override):
            return {"enabled": True, "message": "", "scope": "exception"}
        return {
            "enabled": False,
            "message": global_message or "This feature is currently disabled by the administrator.",
            "scope": "global",
        }
    if not override_enabled:
        return {
            "enabled": False,
            "message": override_message or "This feature is currently disabled for your company.",
            "scope": "company",
        }
    return {"enabled": True, "message": "", "scope": "enabled"}


def get_field_visit_access(company) -> dict[str, Any]:
    settings_obj = SystemSettings.get_settings()
    admin_policy = get_effective_feature_policy(
        settings_obj.feature_policies or {},
        company_id=company.id,
        feature_key=FIELD_VISIT_FEATURE,
    )
    owner_enabled = bool(getattr(company, "field_visit_enabled", True))
    if not admin_policy["enabled"]:
        return {
            **admin_policy,
            "owner_enabled": owner_enabled,
        }
    if not owner_enabled:
        return {
            "enabled": False,
            "message": "",
            "scope": "company_setting",
            "owner_enabled": False,
        }
    return {
        "enabled": True,
        "message": "",
        "scope": "enabled",
        "owner_enabled": True,
    }


def is_field_visit_allowed(company) -> bool:
    return bool(get_field_visit_access(company).get("enabled"))
