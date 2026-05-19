"""Tests for separate OTPIQ integration policy and plan gating."""
import pytest

from integrations.models import SmsProvider, TwilioSettings
from integrations.policy import apply_integration_policy_side_effects, is_integration_allowed
from settings.models import SystemSettings


@pytest.mark.django_db
def test_otpiq_plan_feature_blocks_when_disabled(company, plan, subscription, monkeypatch):
    plan.features = {
        "integration_twilio": True,
        "integration_otpiq": False,
    }
    plan.save(update_fields=["features"])

    assert is_integration_allowed(company, "otpiq") is False
    assert is_integration_allowed(company, "twilio") is True


@pytest.mark.django_db
def test_otpiq_admin_policy_company_override(company, plan, subscription):
    settings = SystemSettings.get_settings()
    settings.integration_policies = {
        "otpiq": {
            "global_enabled": True,
            "global_message": "",
            "company_overrides": {
                str(company.id): {"enabled": False, "message": "OTPIQ disabled for you"},
            },
        },
    }
    settings.save(update_fields=["integration_policies"])

    assert is_integration_allowed(company, "otpiq") is False


@pytest.mark.django_db
def test_disable_otpiq_policy_turns_off_otpiq_settings(company):
    settings = SystemSettings.get_settings()
    settings.integration_policies = {
        "otpiq": {"global_enabled": True, "global_message": "", "company_overrides": {}},
    }
    settings.save(update_fields=["integration_policies"])

    tw = TwilioSettings.objects.create(
        company=company,
        provider=SmsProvider.OTPIQ,
        is_enabled=True,
    )
    tw.set_otpiq_api_key("sk_test")
    tw.save(update_fields=["otpiq_api_key"])

    previous = settings.integration_policies
    new_policies = {
        "otpiq": {"global_enabled": False, "global_message": "Off", "company_overrides": {}},
    }
    settings.integration_policies = new_policies
    settings.save(update_fields=["integration_policies"])
    apply_integration_policy_side_effects(previous_policies=previous, new_policies=new_policies)

    tw.refresh_from_db()
    assert tw.is_enabled is False
