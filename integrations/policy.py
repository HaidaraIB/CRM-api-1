from __future__ import annotations

from typing import Any

from integrations.models import IntegrationAccount, OpenAISettings, SmsProvider, TwilioSettings, WhatsAppAccount
from settings.models import SystemSettings
from subscriptions.entitlements import build_company_entitlements

INTEGRATION_POLICY_PLATFORMS = ("meta", "tiktok", "whatsapp", "twilio", "otpiq", "openai", "api")
PLAN_INTEGRATION_FEATURE_MAP = {
    "meta": "integration_meta",
    "tiktok": "integration_tiktok",
    "whatsapp": "integration_whatsapp",
    "twilio": "integration_twilio",
    "otpiq": "integration_otpiq",
    "openai": "integration_openai",
    "api": "integration_api",
}
SMS_INTEGRATION_PLATFORMS = ("twilio", "otpiq")
INTEGRATION_POLICY_DEFAULTS = {
    "global_enabled": True,
    "global_message": "",
    "company_overrides": {},
}


def _is_global_exception(company_policy: dict[str, Any] | None) -> bool:
    """Explicit allow-list entry when the platform is globally disabled."""
    return isinstance(company_policy, dict) and company_policy.get("enabled") is True


def _global_exception_company_ids(company_overrides: dict[str, Any]) -> list[str]:
    return [
        company_id
        for company_id, company_policy in company_overrides.items()
        if _is_global_exception(company_policy if isinstance(company_policy, dict) else None)
    ]


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
        if _is_global_exception(override):
            return {"enabled": True, "message": "", "scope": "exception"}
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


def is_integration_allowed(company, platform: str) -> bool:
    """True when plan and admin policy allow the integration platform."""
    if not company:
        return False
    plan_gate = get_plan_integration_access(company, platform)
    if not plan_gate["enabled"]:
        return False
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform=platform,
    )
    return bool(effective["enabled"])


def is_any_sms_integration_allowed(company) -> bool:
    """True if Twilio or OTPIQ SMS integration is allowed for this company."""
    return any(is_integration_allowed(company, platform) for platform in SMS_INTEGRATION_PLATFORMS)


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


def _disable_company_platform_integrations(*, company_id: str | int, platform: str) -> None:
    IntegrationAccount.objects.filter(
        company_id=company_id,
        platform=platform,
        is_active=True,
    ).update(is_active=False)
    if platform == "whatsapp":
        WhatsAppAccount.objects.filter(company_id=company_id, status="connected").update(status="disconnected")
    if platform == "twilio":
        TwilioSettings.objects.filter(
            company_id=company_id,
            provider=SmsProvider.TWILIO,
            is_enabled=True,
        ).update(is_enabled=False)
    if platform == "otpiq":
        TwilioSettings.objects.filter(
            company_id=company_id,
            provider=SmsProvider.OTPIQ,
            is_enabled=True,
        ).update(is_enabled=False)
    if platform == "openai":
        OpenAISettings.objects.filter(
            company_id=company_id,
            is_enabled=True,
        ).update(is_enabled=False)


def apply_integration_policy_side_effects(*, previous_policies: dict[str, Any] | None, new_policies: dict[str, Any] | None) -> None:
    previous_policies = previous_policies or {}
    new_policies = new_policies or {}

    for platform in INTEGRATION_POLICY_PLATFORMS:
        prev = _get_platform_policy(previous_policies, platform)
        curr = _get_platform_policy(new_policies, platform)

        # Global disable transition (keep explicit company exceptions active)
        if prev["global_enabled"] and not curr["global_enabled"]:
            exception_company_ids = _global_exception_company_ids(curr["company_overrides"])
            account_qs = IntegrationAccount.objects.filter(platform=platform, is_active=True)
            if exception_company_ids:
                account_qs = account_qs.exclude(company_id__in=exception_company_ids)
            account_qs.update(is_active=False)
            if platform == "whatsapp":
                wa_qs = WhatsAppAccount.objects.filter(status="connected")
                if exception_company_ids:
                    wa_qs = wa_qs.exclude(company_id__in=exception_company_ids)
                wa_qs.update(status="disconnected")
            if platform == "twilio":
                tw_qs = TwilioSettings.objects.filter(provider=SmsProvider.TWILIO, is_enabled=True)
                if exception_company_ids:
                    tw_qs = tw_qs.exclude(company_id__in=exception_company_ids)
                tw_qs.update(is_enabled=False)
            if platform == "otpiq":
                otpiq_qs = TwilioSettings.objects.filter(provider=SmsProvider.OTPIQ, is_enabled=True)
                if exception_company_ids:
                    otpiq_qs = otpiq_qs.exclude(company_id__in=exception_company_ids)
                otpiq_qs.update(is_enabled=False)
            if platform == "openai":
                openai_qs = OpenAISettings.objects.filter(is_enabled=True)
                if exception_company_ids:
                    openai_qs = openai_qs.exclude(company_id__in=exception_company_ids)
                openai_qs.update(is_enabled=False)

        # Company-level disable / exception removal transitions
        prev_overrides = prev["company_overrides"]
        curr_overrides = curr["company_overrides"]
        for company_id in set(prev_overrides.keys()) | set(curr_overrides.keys()):
            prev_policy = prev_overrides.get(company_id) or {}
            curr_policy = curr_overrides.get(company_id) or {}

            if not curr["global_enabled"]:
                if _is_global_exception(prev_policy) and not _is_global_exception(curr_policy):
                    _disable_company_platform_integrations(company_id=company_id, platform=platform)
                continue

            prev_enabled = bool(prev_policy.get("enabled", True))
            curr_enabled = bool(curr_policy.get("enabled", True))
            if prev_enabled and not curr_enabled:
                _disable_company_platform_integrations(company_id=company_id, platform=platform)
