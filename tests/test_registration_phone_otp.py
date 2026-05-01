"""
Registration phone OTP channel policy and send-otp branching.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from unittest.mock import patch

from accounts.phone_otp_policy import (
    PHONE_OTP_CHANNEL_CACHE_KEY,
    PHONE_OTP_REQUIRED_CACHE_KEY,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
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
    cache.set(PHONE_OTP_REQUIRED_CACHE_KEY, True, timeout=None)
    cache.set(PHONE_OTP_CHANNEL_CACHE_KEY, "twilio_sms", timeout=None)

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
