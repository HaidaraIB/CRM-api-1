"""Helpers for platform auth policy tests (SystemSettings-backed)."""
from settings.models import SystemSettings


def _settings():
    return SystemSettings.get_settings()


def reset_platform_auth_settings():
    settings = _settings()
    settings.registration_phone_otp_required = False
    settings.registration_phone_otp_channel = ""
    settings.registration_email_verification_required = False
    settings.save(
        update_fields=[
            "registration_phone_otp_required",
            "registration_phone_otp_channel",
            "registration_email_verification_required",
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
