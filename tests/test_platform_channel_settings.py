"""Admin panel saves Platform WhatsApp and Twilio as a partial PATCH."""

import pytest
from rest_framework.test import APIClient

from accounts.models import User
from settings.models import PlatformTwilioSettings, PlatformWhatsAppSettings


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
def test_platform_whatsapp_patch_updates_only_sent_fields(super_admin_client):
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
