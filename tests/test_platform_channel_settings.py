"""Admin panel saves Platform WhatsApp and Twilio as a partial PATCH."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from accounts.models import User
from settings.models import PlatformOTPIQSettings, PlatformTwilioSettings, PlatformWhatsAppSettings


@pytest.fixture
def super_admin_client(db):
    user = User.objects.create_user(
        username="platform_settings_admin",
        email="platform-settings@test.com",
        password="testpass123",
        first_name="Platform",
        last_name="Admin",
        role="admin",
        is_superuser=True,
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
@patch(
    "settings.credential_validation.validate_whatsapp_platform_credentials",
    return_value={},
)
def test_platform_whatsapp_patch_updates_only_sent_fields(
    _mock_whatsapp_validate, super_admin_client
):
    settings_row = PlatformWhatsAppSettings.get_settings()
    settings_row.phone_number_id = "111"
    settings_row.graph_api_version = "v25.0"
    settings_row.otp_template_name = "otp_code"
    settings_row.save()

    response = super_admin_client.patch(
        "/api/v1/settings/platform-whatsapp/1/",
        {"phone_number_id": "222"},
        format="json",
    )

    assert response.status_code == 200
    settings_row.refresh_from_db()
    assert settings_row.phone_number_id == "222"
    assert settings_row.graph_api_version == "v25.0"
    assert settings_row.otp_template_name == "otp_code"


@pytest.mark.django_db
def test_platform_twilio_patch_updates_only_sent_fields(super_admin_client):
    settings_row = PlatformTwilioSettings.get_settings()
    settings_row.account_sid = "ACorig"
    settings_row.twilio_number = "+15550001111"
    settings_row.is_enabled = False
    settings_row.save()

    response = super_admin_client.patch(
        "/api/v1/settings/platform-twilio/1/",
        {"is_enabled": True},
        format="json",
    )

    assert response.status_code == 200
    settings_row.refresh_from_db()
    assert settings_row.is_enabled is True
    assert settings_row.account_sid == "ACorig"
    assert settings_row.twilio_number == "+15550001111"


@pytest.mark.django_db
@patch("settings.credential_validation.validate_otpiq_credentials", return_value={})
def test_platform_otpiq_patch_masks_api_key(_mock_otpiq_validate, super_admin_client):
    settings_row = PlatformOTPIQSettings.get_settings()
    settings_row.set_api_key("sk-live-test-key")
    settings_row.sender_id = "LOOP"
    settings_row.save()

    get_response = super_admin_client.get("/api/v1/settings/platform-otpiq/1/")
    assert get_response.status_code == 200
    body = get_response.json()["data"]
    assert body["api_key_masked"] == "********"
    assert "api_key" not in body or body.get("api_key") is None

    patch_response = super_admin_client.patch(
        "/api/v1/settings/platform-otpiq/1/",
        {"sender_id": "LOOP2"},
        format="json",
    )
    assert patch_response.status_code == 200
    settings_row.refresh_from_db()
    assert settings_row.sender_id == "LOOP2"
    assert settings_row.get_api_key() == "sk-live-test-key"
