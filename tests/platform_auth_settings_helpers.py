"""Helpers for platform auth policy tests (SystemSettings-backed)."""
from settings.models import SystemSettings


def _settings():
    return SystemSettings.get_settings()


def reset_platform_auth_settings():
    settings = _settings()
    settings.registration_phone_otp_required = False
    settings.registration_phone_otp_channel = ""
    settings.registration_email_verification_required = False
    settings.login_lockout_enabled = True
    settings.login_max_failed_attempts = 5
    settings.login_lockout_duration_minutes = 15
    settings.save(
        update_fields=[
            "registration_phone_otp_required",
            "registration_phone_otp_channel",
            "registration_email_verification_required",
            "login_lockout_enabled",
            "login_max_failed_attempts",
            "login_lockout_duration_minutes",
            "updated_at",
        ]
    )


def set_registration_phone_otp_required(required: bool, channel: str = ""):
    settings = _settings()
    settings.registration_phone_otp_required = required
    settings.registration_phone_otp_channel = channel if required else ""
    settings.save(
        update_fields=[
            "registration_phone_otp_required",
            "registration_phone_otp_channel",
            "updated_at",
        ]
    )


def set_registration_email_verification_required(required: bool):
    settings = _settings()
    settings.registration_email_verification_required = required
    settings.save(
        update_fields=["registration_email_verification_required", "updated_at"]
    )


def set_login_lockout_policy(
    *,
    enabled: bool = True,
    max_attempts: int = 5,
    duration_minutes: int = 15,
):
    settings = _settings()
    settings.login_lockout_enabled = enabled
    settings.login_max_failed_attempts = max_attempts
    settings.login_lockout_duration_minutes = duration_minutes
    settings.save(
        update_fields=[
            "login_lockout_enabled",
            "login_max_failed_attempts",
            "login_lockout_duration_minutes",
            "updated_at",
        ]
    )
