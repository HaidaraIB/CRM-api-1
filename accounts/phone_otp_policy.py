"""
Runtime registration phone OTP policy (admin-controlled via cache).
"""
from django.core.cache import cache

from .platform_whatsapp import platform_whatsapp_configured

# Legacy key name kept for backward compatibility with existing deployments.
PHONE_OTP_REQUIRED_CACHE_KEY = "platform_whatsapp_otp_required_override"
PHONE_OTP_CHANNEL_CACHE_KEY = "registration_phone_otp_channel"

CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_TWILIO_SMS = "twilio_sms"
VALID_CHANNELS = frozenset({CHANNEL_WHATSAPP, CHANNEL_TWILIO_SMS})


def effective_phone_otp_required() -> bool:
    return bool(cache.get(PHONE_OTP_REQUIRED_CACHE_KEY, False))


def effective_phone_otp_channel():
    """Active delivery channel when OTP is required; None if OTP off or not set."""
    if not effective_phone_otp_required():
        return None
    ch = cache.get(PHONE_OTP_CHANNEL_CACHE_KEY)
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
