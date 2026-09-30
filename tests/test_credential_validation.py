"""Live credential validation on settings save (mocked providers)."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from accounts.platform_registration_sms import send_registration_otp_sms
from settings.credential_validation import (
    validate_otpiq_credentials,
    validate_resend_outbound_email,
    validate_twilio_credentials,
)
from settings.models import PlatformTwilioSettings, SystemSettings

User = get_user_model()


@pytest.fixture
def super_admin_client(db):
    user = User.objects.create_user(
        username="cred_val_admin",
        email="cred_val@test.com",
        password="testpass123",
        first_name="Admin",
        last_name="User",
        role="admin",
        is_superuser=True,
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_validate_twilio_rejects_bad_sender_id_format():
    errors = validate_twilio_credentials(
        account_sid="ACxxx",
        auth_token="token",
        twilio_number="",
        sender_id="LOOP-CRM!",
    )
    assert "sender_id" in errors


@patch("twilio.rest.Client")
def test_validate_twilio_rejects_unknown_number(mock_client_cls):
    client = MagicMock()
    mock_client_cls.return_value = client
    client.api.accounts.return_value.fetch.return_value = None
    client.incoming_phone_numbers.list.return_value = []

    errors = validate_twilio_credentials(
        account_sid="ACxxx",
        auth_token="token",
        twilio_number="+15551234567",
        sender_id="",
    )
    assert "twilio_number" in errors


@patch("twilio.rest.Client")
def test_validate_twilio_accepts_number_on_account(mock_client_cls):
    client = MagicMock()
    mock_client_cls.return_value = client
    client.api.accounts.return_value.fetch.return_value = None
    client.incoming_phone_numbers.list.return_value = [MagicMock()]

    errors = validate_twilio_credentials(
        account_sid="ACxxx",
        auth_token="token",
        twilio_number="+15551234567",
        sender_id="LOOPCRM",
    )
    assert errors == {}


@patch("integrations.services.otpiq_sms.fetch_sender_ids")
def test_validate_otpiq_rejects_bad_api_key(mock_fetch):
    mock_fetch.return_value = (None, "Unauthorized")

    errors = validate_otpiq_credentials(api_key="bad", sender_id="")
    assert "api_key" in errors


@pytest.mark.django_db
@patch("twilio.rest.Client")
def test_platform_twilio_patch_rejects_invalid_number(mock_client_cls, super_admin_client):
    client = MagicMock()
    mock_client_cls.return_value = client
    client.api.accounts.return_value.fetch.return_value = None
    client.incoming_phone_numbers.list.return_value = []

    row = PlatformTwilioSettings.get_settings()
    row.account_sid = "ACtest"
    row.set_auth_token("secret")
    row.twilio_number = "+15550001111"
    row.save()

    response = super_admin_client.patch(
        "/api/v1/settings/platform-twilio/1/",
        {"twilio_number": "+19998887777"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["success"] is False


@pytest.mark.django_db
def test_registration_otp_sms_uses_twilio_number_only():
    row = PlatformTwilioSettings.get_settings()
    row.account_sid = "ACtest"
    row.set_auth_token("secret")
    row.twilio_number = "+15551234567"
    row.sender_id = "LOOPCRM"
    row.save()

    created = {}

    class FakeMsg:
        sid = "SM123"

    class FakeMessages:
        def create(self, **kwargs):
            created.update(kwargs)
            return FakeMsg()

    class FakeClient:
        messages = FakeMessages()

    with patch("twilio.rest.Client", return_value=FakeClient()):
        ok, _details = send_registration_otp_sms("+9647501234567", "123456", 10)

    assert ok is True
    assert created.get("from_") == "+15551234567"


@pytest.mark.django_db
@patch("settings.credential_validation.requests.get")
def test_resend_validation_requires_verified_domain(mock_get):
    from django.test import override_settings

    mock_get.return_value.status_code = 200
    mock_get.return_value.content = b'{"data": [{"name": "example.com", "status": "verified"}]}'
    mock_get.return_value.json.return_value = {
        "data": [{"name": "example.com", "status": "verified"}],
    }

    with override_settings(RESEND_API_KEY="re_test"):
        errors = validate_resend_outbound_email(
            is_active=True,
            from_email="noreply@other.com",
        )
    assert "from_email" in errors


@pytest.mark.django_db
def test_platform_twilio_requires_number_when_registration_uses_twilio(super_admin_client):
    settings = SystemSettings.get_settings()
    settings.registration_phone_otp_required = True
    settings.registration_phone_otp_channel = "twilio_sms"
    settings.save()

    row = PlatformTwilioSettings.get_settings()
    row.account_sid = "ACtest"
    row.set_auth_token("secret")
    row.twilio_number = ""
    row.sender_id = "LOOPCRM"
    row.save()

    with patch("twilio.rest.Client") as mock_client_cls:
        client = MagicMock()
        mock_client_cls.return_value = client
        client.api.accounts.return_value.fetch.return_value = None

        response = super_admin_client.patch(
            "/api/v1/settings/platform-twilio/1/",
            {"sender_id": "LOOPCRM"},
            format="json",
        )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False


@pytest.mark.django_db
@patch("twilio.rest.Client")
def test_company_twilio_settings_save_rejects_invalid_sender_id(
    mock_client_cls, authenticated_admin, company
):
    from integrations.models import SmsProvider, TwilioSettings

    client = MagicMock()
    mock_client_cls.return_value = client
    client.api.accounts.return_value.fetch.return_value = None
    client.incoming_phone_numbers.list.return_value = [MagicMock()]

    tw = TwilioSettings.objects.create(
        company=company,
        provider=SmsProvider.TWILIO,
        account_sid="ACtest",
        twilio_number="+15551234567",
        is_enabled=True,
    )
    tw.set_auth_token("tok")
    tw.save()

    response = authenticated_admin.put(
        "/api/v1/integrations/twilio/settings/",
        {"sender_id": "LOOP-CRM!"},
        format="json",
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
