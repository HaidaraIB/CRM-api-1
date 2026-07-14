"""
Runtime registration phone OTP policy (admin-controlled via SystemSettings).
"""
from settings.models import SystemSettings

from .platform_whatsapp import platform_whatsapp_configured

CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_TWILIO_SMS = "twilio_sms"
VALID_CHANNELS = frozenset({CHANNEL_WHATSAPP, CHANNEL_TWILIO_SMS})


def effective_phone_otp_required() -> bool:
    return bool(SystemSettings.get_settings().registration_phone_otp_required)


def effective_phone_otp_channel():
    """Active delivery channel when OTP is required; None if OTP off or not set."""
    if not effective_phone_otp_required():
        return None
    ch = (SystemSettings.get_settings().registration_phone_otp_channel or "").strip().lower()
    if ch in VALID_CHANNELS:
        return ch
    return None


def platform_twilio_ready_for_registration_otp() -> bool:
    """Same credential completeness as SMS broadcast, but ignores is_enabled."""
    from settings.models import PlatformTwilioSettings

    tw = PlatformTwilioSettings.get_settings()
    account_sid = (tw.account_sid or "").strip()
    auth_token = tw.get_auth_token()
    twilio_number = (tw.twilio_number or "").strip()
    sender_id = (tw.sender_id or "").strip()
    from_value = sender_id if sender_id else twilio_number
    return bool(account_sid and auth_token and from_value)


def channel_is_configured(channel: str) -> bool:
    if channel == CHANNEL_WHATSAPP:
        return platform_whatsapp_configured()
    if channel == CHANNEL_TWILIO_SMS:
        return platform_twilio_ready_for_registration_otp()
    return False
