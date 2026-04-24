"""
Tests for Resend outbound email backend and connection factory.
"""

from unittest.mock import patch

import pytest
from django.core.mail import EmailMultiAlternatives
from django.test import override_settings

from types import SimpleNamespace

from crm_saas_api.email_exceptions import OutboundEmailNotConfiguredError, SMTPNotActiveError
from crm_saas_api.resend_email_backend import ResendEmailBackend, _message_to_resend_params
from crm_saas_api.utils import (
    format_platform_from_address,
    get_platform_email_display_name,
    get_smtp_connection,
)
from settings.models import SMTPSettings


@pytest.fixture
def smtp_settings_active(db):
    s = SMTPSettings.get_settings()
    s.is_active = True
    s.from_email = "noreply@example.com"
    s.from_name = "CRM"
    s.host = "unused"
    s.port = 587
    s.username = "unused"
    s.password = "unused"
    s.use_tls = True
    s.use_ssl = False
    s.save()
    return s


@pytest.mark.django_db
@override_settings(RESEND_API_KEY="re_test_key")
def test_get_smtp_connection_returns_resend_backend(smtp_settings_active):
    conn = get_smtp_connection()
    assert isinstance(conn, ResendEmailBackend)
    assert conn.api_key == "re_test_key"


@pytest.mark.django_db
@override_settings(RESEND_API_KEY="")
def test_get_smtp_connection_requires_api_key(smtp_settings_active):
    with pytest.raises(OutboundEmailNotConfiguredError) as exc:
        get_smtp_connection()
    assert "RESEND_API_KEY" in str(exc.value)


@pytest.mark.django_db
@override_settings(RESEND_API_KEY="re_x")
def test_get_smtp_connection_requires_active_flag(db):
    s = SMTPSettings.get_settings()
    s.is_active = False
    s.save()
    with pytest.raises(OutboundEmailNotConfiguredError):
        get_smtp_connection()


def test_smtp_not_active_error_alias():
    assert SMTPNotActiveError is OutboundEmailNotConfiguredError


def test_platform_display_name_defaults_to_loop_crm():
    row = SimpleNamespace(from_name="", from_email="noreply@mail.example.com")
    assert get_platform_email_display_name(row) == "LOOP CRM"
    assert format_platform_from_address(row) == "LOOP CRM <noreply@mail.example.com>"


def test_platform_display_name_respects_custom_from_name():
    row = SimpleNamespace(from_name="  Custom  ", from_email="a@b.co")
    assert get_platform_email_display_name(row) == "Custom"
    assert format_platform_from_address(row) == "Custom <a@b.co>"


def test_message_to_resend_params_html_and_text():
    msg = EmailMultiAlternatives(
        subject="Hello",
        body="Plain body",
        from_email="CRM <noreply@example.com>",
        to=["user@example.com"],
    )
    msg.attach_alternative("<p>Hi</p>", "text/html")
    params = _message_to_resend_params(msg)
    assert params["from"] == "CRM <noreply@example.com>"
    assert params["to"] == ["user@example.com"]
    assert params["subject"] == "Hello"
    assert params["html"] == "<p>Hi</p>"
    assert params["text"] == "Plain body"


@pytest.mark.django_db
@override_settings(RESEND_API_KEY="re_test_key")
@patch("resend.Emails.send")
def test_resend_backend_send_messages_calls_api(mock_send, smtp_settings_active):
    mock_send.return_value = type("R", (), {"id": "email-id-1"})()

    backend = ResendEmailBackend(api_key="re_test_key", fail_silently=False)
    msg = EmailMultiAlternatives(
        subject="Subj",
        body="Text",
        from_email="noreply@example.com",
        to=["a@b.com"],
    )
    msg.attach_alternative("<b>x</b>", "text/html")

    n = backend.send_messages([msg])
    assert n == 1
    mock_send.assert_called_once()
    call_kw = mock_send.call_args[0][0]
    assert call_kw["to"] == ["a@b.com"]
    assert call_kw["subject"] == "Subj"
    assert "html" in call_kw
