"""
Registration phone OTP channel policy and send-otp branching.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from unittest.mock import MagicMock, patch

from tests.platform_auth_settings_helpers import (
    reset_platform_auth_settings,
    set_registration_phone_otp_required,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_auth_settings():
    reset_platform_auth_settings()
    cache.clear()


@pytest.mark.django_db
def test_phone_otp_requirement_post_rejects_whatsapp_when_not_configured(api_client):
    admin = User.objects.create_superuser(
        username="otp_admin_wa",
        email="otp_admin_wa@test.com",
        password="secret12345",
    )
    api_client.force_authenticate(user=admin)
    url = reverse("phone_otp_requirement_settings")
    with patch(
        "accounts.phone_otp_policy.platform_whatsapp_configured",
        return_value=False,
    ):
        r = api_client.post(
            url,
            {"phone_otp_required": True, "phone_otp_channel": "whatsapp"},
            format="json",
        )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["success"] is False
    assert r.data["error"]["code"] == "whatsapp_otp_not_configured"


@pytest.mark.django_db
def test_phone_otp_requirement_post_rejects_twilio_when_not_ready(api_client):
    admin = User.objects.create_superuser(
        username="otp_admin_tw",
        email="otp_admin_tw@test.com",
        password="secret12345",
    )
    api_client.force_authenticate(user=admin)
    url = reverse("phone_otp_requirement_settings")
    with patch(
        "accounts.phone_otp_policy.platform_twilio_ready_for_registration_otp",
        return_value=False,
    ):
        r = api_client.post(
            url,
            {"phone_otp_required": True, "phone_otp_channel": "twilio_sms"},
            format="json",
        )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["success"] is False
    assert r.data["error"]["code"] == "twilio_otp_not_configured"


@pytest.mark.django_db
def test_register_phone_send_otp_uses_twilio_sms_branch(api_client):
    set_registration_phone_otp_required(True, channel="twilio_sms")

    sent = []

    def fake_send(to_e164, code, expire_minutes):
        sent.append((to_e164, code, expire_minutes))
        return True, {}

    url = reverse("register_phone_send_otp")
    with patch(
        "accounts.views.phone_registration.platform_twilio_ready_for_registration_otp",
        return_value=True,
    ):
        with patch(
            "accounts.views.phone_registration.send_registration_otp_sms",
            fake_send,
        ):
            r = api_client.post(
                url,
                {"phone": "+966501234567"},
                format="json",
            )

    assert r.status_code == status.HTTP_200_OK
    assert r.data["success"] is True
    assert r.data["data"]["channel"] == "twilio_sms"
    assert len(sent) == 1
    assert len(sent[0][1]) == 6  # 6-digit code


@pytest.mark.django_db
def test_phone_otp_requirement_post_rejects_otpiq_when_not_ready(api_client):
    admin = User.objects.create_superuser(
        username="otp_admin_otpiq",
        email="otp_admin_otpiq@test.com",
        password="secret12345",
    )
    api_client.force_authenticate(user=admin)
    url = reverse("phone_otp_requirement_settings")
    with patch(
        "accounts.phone_otp_policy.platform_otpiq_ready_for_registration_otp",
        return_value=False,
    ):
        r = api_client.post(
            url,
            {"phone_otp_required": True, "phone_otp_channel": "otpiq"},
            format="json",
        )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["success"] is False
    assert r.data["error"]["code"] == "otpiq_otp_not_configured"


@pytest.mark.django_db
def test_register_phone_send_otp_uses_otpiq_branch(api_client):
    set_registration_phone_otp_required(True, channel="otpiq")

    captured = {}

    def fake_send(phone_raw, code):
        captured["phone"] = phone_raw
        captured["code"] = code
        return True, {}

    url = reverse("register_phone_send_otp")
    with patch(
        "accounts.views.phone_registration.platform_otpiq_ready_for_registration_otp",
        return_value=True,
    ):
        with patch(
            "accounts.views.phone_registration.send_registration_otp_otpiq",
            fake_send,
        ):
            r = api_client.post(
                url,
                {"phone": "+9647501234567"},
                format="json",
            )

    assert r.status_code == status.HTTP_200_OK
    assert r.data["success"] is True
    assert r.data["data"]["channel"] == "otpiq"
    assert captured["phone"] == "+9647501234567"
    assert len(captured["code"]) == 6


@pytest.mark.django_db
def test_register_phone_send_otp_otpiq_failure_returns_424(api_client):
    set_registration_phone_otp_required(True, channel="otpiq")

    url = reverse("register_phone_send_otp")
    with patch(
        "accounts.views.phone_registration.platform_otpiq_ready_for_registration_otp",
        return_value=True,
    ):
        with patch(
            "accounts.views.phone_registration.send_registration_otp_otpiq",
            return_value=(False, {"error": "rejected"}),
        ):
            r = api_client.post(
                url,
                {"phone": "+9647501234567"},
                format="json",
            )

    assert r.status_code == status.HTTP_424_FAILED_DEPENDENCY
    assert r.data["error"]["code"] == "otpiq_send_failed"


@pytest.mark.django_db
@patch("integrations.services.otpiq_sms.requests.post")
def test_registration_otpiq_send_uses_sms_provider_only(mock_post):
    from accounts.platform_registration_otpiq import send_registration_otp_otpiq
    from settings.models import PlatformOTPIQSettings

    row = PlatformOTPIQSettings.get_settings()
    row.set_api_key("sk_test")
    row.save()

    mock_post.return_value = MagicMock(
        status_code=200,
        content=b'{"smsId": "sms-reg"}',
        json=lambda: {"smsId": "sms-reg"},
    )

    ok, _details = send_registration_otp_otpiq("+9647501234567", "123456")
    assert ok is True
    payload = mock_post.call_args[1]["json"]
    assert payload["provider"] == "sms"
    assert payload["smsType"] == "verification"
