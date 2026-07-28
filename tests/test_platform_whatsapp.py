"""Tests for platform WhatsApp helpers used by Company WhatsApp."""

import pytest

from accounts.platform_whatsapp import (
    _looks_like_template_name,
    _strip_env_value,
    effective_admin_template_name,
    platform_access_token_looks_valid,
    send_admin_message,
)


def test_strip_env_value_removes_quotes():
    assert _strip_env_value('  "abc"  ') == "abc"
    assert _strip_env_value("'xyz'") == "xyz"
    assert _strip_env_value("plain") == "plain"


def test_looks_like_template_name():
    assert _looks_like_template_name("admin_notify_1") is True
    assert _looks_like_template_name("100000000000000") is False
    assert _looks_like_template_name("") is False


@pytest.mark.django_db
def test_digit_only_admin_template_ignored(settings):
    settings.PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME = "100000000000000"
    assert effective_admin_template_name() == ""


@pytest.mark.django_db
def test_send_admin_rejects_short_token(settings):
    settings.PLATFORM_WHATSAPP_PHONE_NUMBER_ID = "123456"
    settings.PLATFORM_WHATSAPP_ACCESS_TOKEN = "short-token"
    settings.PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME = ""
    ok, details = send_admin_message("9647715952996", "hello")
    assert ok is False
    assert details["error"] == "platform_whatsapp_token_invalid"
    assert platform_access_token_looks_valid() is False
