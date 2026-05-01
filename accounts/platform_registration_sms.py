"""Send registration OTP via platform Twilio (programmable SMS)."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def send_registration_otp_sms(to_e164: str, code: str, expire_minutes: int = 10) -> tuple[bool, Any]:
    """
    Send a plain SMS with the numeric OTP using PlatformTwilioSettings.
    Does not require is_enabled (broadcast flag); only complete credentials.
    """
    from settings.models import PlatformTwilioSettings
    from twilio.base.exceptions import TwilioRestException
    from twilio.rest import Client as TwilioClient

    tw = PlatformTwilioSettings.get_settings()
    account_sid = (tw.account_sid or "").strip()
    auth_token = tw.get_auth_token()
    twilio_number = (tw.twilio_number or "").strip()
    sender_id = (tw.sender_id or "").strip()
    from_value = sender_id if sender_id else (twilio_number or "")
    if not account_sid or not auth_token or not from_value:
        return False, {"error": "twilio_otp_not_configured"}

    body = f"Your verification code is {code}. It expires in {expire_minutes} minutes."
    try:
        client = TwilioClient(account_sid, auth_token)
        msg = client.messages.create(body=body, from_=from_value, to=to_e164)
        return True, {"sid": getattr(msg, "sid", None)}
    except TwilioRestException as e:
        logger.warning("Twilio registration OTP SMS failed: %s", e)
        return False, {"error": getattr(e, "msg", str(e)), "code": getattr(e, "code", None)}
    except Exception as e:
        logger.exception("Twilio registration OTP SMS failed")
        return False, {"error": str(e)}
