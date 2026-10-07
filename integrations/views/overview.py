"""One request for the integrations hub: status of every integration the tenant can open."""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import success_response
from integrations.models import (
    CompanyLeadApiKey,
    IntegrationAccount,
    OpenAISettings,
    TwilioSettings,
)
from integrations.policy import get_effective_integration_policy, get_plan_integration_access
from settings.models import SystemSettings

# Hub cards. SMS covers Twilio and OTPIQ; the card is on when either provider is allowed.
OVERVIEW_KEYS = (
    ("meta", "meta"),
    ("tiktok", "tiktok"),
    ("whatsapp", "whatsapp"),
    ("sms", "twilio"),
    ("openai", "openai"),
    ("api", "api"),
    ("mujeb", "mujeb"),
)


def _policy_on(company, policies, platform: str) -> bool:
    plan_gate = get_plan_integration_access(company, platform)
    if not plan_gate["enabled"]:
        return False
    effective = get_effective_integration_policy(policies, company_id=company.id, platform=platform)
    return effective.get("enabled") is not False


def _account_row(company, platform: str) -> dict:
    account = (
        IntegrationAccount.objects.filter(company=company, platform=platform)
        .order_by("-id")
        .first()
    )
    if account is None:
        return {"status": "disconnected", "account_name": "", "last_activity_at": None}
    return {
        "status": account.status or "disconnected",
        "account_name": account.name or "",
        "last_activity_at": account.last_sync_at.isoformat() if account.last_sync_at else None,
    }


def _iso(value):
    return value.isoformat() if value else None


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def integration_overview_view(request):
    company = request.user.company
    policies = SystemSettings.get_settings().integration_policies or {}
    rows = []
    for key, policy_platform in OVERVIEW_KEYS:
        if key == "sms":
            settings_row = TwilioSettings.objects.filter(company=company).first()
            enabled = bool(settings_row and settings_row.is_enabled)
            policy_on = _policy_on(company, policies, "twilio") or _policy_on(company, policies, "otpiq")
            rows.append({
                "key": key,
                "status": "connected" if enabled else "disconnected",
                "account_name": "",
                "last_activity_at": _iso(settings_row.updated_at) if settings_row else None,
                "policy_enabled": policy_on,
            })
            continue
        if key == "openai":
            settings_row = OpenAISettings.objects.filter(company=company).first()
            enabled = bool(settings_row and settings_row.is_enabled and settings_row.api_key)
            rows.append({
                "key": key,
                "status": "connected" if enabled else "disconnected",
                "account_name": "",
                "last_activity_at": _iso(getattr(settings_row, "updated_at", None)) if settings_row else None,
                "policy_enabled": _policy_on(company, policies, policy_platform),
            })
            continue
        if key in ("api", "mujeb"):
            latest = (
                CompanyLeadApiKey.objects.filter(company=company, is_active=True)
                .order_by("-last_used_at", "-created_at")
                .first()
            )
            rows.append({
                "key": key,
                "status": "connected" if latest else "disconnected",
                "account_name": latest.name if latest else "",
                "last_activity_at": _iso(latest.last_used_at or latest.created_at) if latest else None,
                "policy_enabled": _policy_on(company, policies, policy_platform),
            })
            continue
        row = _account_row(company, key)
        row["key"] = key
        row["policy_enabled"] = _policy_on(company, policies, policy_platform)
        rows.append(row)
    return success_response(data=rows)
