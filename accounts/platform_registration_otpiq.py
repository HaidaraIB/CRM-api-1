import logging
from typing import Any

logger = logging.getLogger(__name__)


def send_registration_otp_otpiq(phone_raw: str, code: str) -> tuple[bool, Any]:
    """
    Send registration OTP via platform OTPIQ settings.
    """
    from integrations.services.otpiq_sms import send_verification_code
    from settings.models import PlatformOTPIQSettings

    row = PlatformOTPIQSettings.get_settings()
    api_key = row.get_api_key()
    if not (api_key or "").strip():
        return False, {"error": "otpiq_otp_not_configured"}

    sender_id = (row.sender_id or "").strip() or None
    ok, _sms_id, error_key, error_message = send_verification_code(
        api_key=api_key,
        phone=phone_raw,
        code=code,
        sender_id=sender_id,
        route_provider="sms",
    )
    if ok:
        return True, {}
    return False, {
        "error": error_message or "send_failed",
        "error_key": error_key,
    }
