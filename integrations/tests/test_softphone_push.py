"""Tests for softphone push deduplication."""

from unittest.mock import patch

import pytest

from integrations.services import softphone_push


@pytest.mark.django_db
def test_softphone_push_dedupe_skips_second_delivery(employee_user):
    user = employee_user
    with patch.object(softphone_push, "send_apns_voip_push", return_value=(False, None)):
        with patch.object(
            softphone_push.NotificationService,
            "send_softphone_call_push",
            return_value=True,
        ) as mock_fcm:
            first = softphone_push.send_softphone_incoming_push(
                user,
                caller="+15551234567",
                extension="101",
                call_id=42,
            )
            second = softphone_push.send_softphone_incoming_push(
                user,
                caller="+15551234567",
                extension="101",
                call_id=42,
            )

    assert first is True
    assert second is False
    assert mock_fcm.call_count == 1


def test_softphone_push_dedupe_key_prefers_call_id():
    key = softphone_push._softphone_push_dedupe_key(7, 99, "uuid-abc")
    assert key == "softphone_push_sent:7:99"


def test_apns_voip_status_reports_missing_key(settings):
    settings.APNS_VOIP_KEY_CONTENT = ""
    settings.APNS_VOIP_KEY_PATH = ""
    settings.APNS_VOIP_KEY_ID = ""
    settings.APNS_VOIP_TEAM_ID = ""
    status = softphone_push.apns_voip_status()
    assert status["configured"] is False
    assert status["key_source"] == "missing"


@pytest.mark.django_db
def test_apns_bad_device_token_deletes_device(company):
    from accounts.models import User
    from integrations.models import UserSoftphoneDevice

    user = User.objects.create_user(
        username="apns_user",
        email="apns@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    device = UserSoftphoneDevice.objects.create(
        company=company,
        user=user,
        platform="ios",
        voip_token="deadtoken1234567890",
    )
    with patch.object(softphone_push, "_apns_voip_configured", return_value=True):
        with patch.object(softphone_push, "_apns_voip_jwt", return_value="jwt"):
            with patch("httpx.Client") as mock_client_cls:
                mock_resp = mock_client_cls.return_value.__enter__.return_value.post.return_value
                mock_resp.status_code = 410
                mock_resp.text = "BadDeviceToken"
                ok, status = softphone_push.send_apns_voip_push(
                    "deadtoken1234567890",
                    {"aps": {"content-available": 1}},
                )
    assert ok is False
    assert status == 410
    assert not UserSoftphoneDevice.objects.filter(pk=device.pk).exists()
