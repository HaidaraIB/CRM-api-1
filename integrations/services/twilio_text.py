"""Twilio error normalization helpers for SMS / messaging views."""
import re


def strip_ansi(text):
    if not text or not isinstance(text, str):
        return text or ""
    return re.sub(r"\x1b\[[0-9;]*m", "", text).strip()


def twilio_error_to_key(e):
    code = getattr(e, "code", None)
    msg = (getattr(e, "msg", None) or str(e)).lower()
    if code == 21606 or code == 21608 or ("from" in msg and "not a valid" in msg):
        return "sms_error_invalid_from_number"
    if code == 21211 or "invalid" in msg and ("to" in msg or "recipient" in msg):
        return "sms_error_invalid_to_number"
    if code == 20003 or "authentic" in msg or "credentials" in msg or "unauthorized" in msg:
        return "sms_error_auth"
    if code == 90010 or "inactive" in msg:
        return "sms_error_account_inactive"
    if code == 20429 or "too many" in msg or "rate" in msg:
        return "sms_error_rate_limit"
    if code == 21614 or "not a valid mobile" in msg:
        return "sms_error_invalid_mobile"
    if code == 21408 or "permission" in msg or "unverified" in msg:
        return "sms_error_permission"
    if "blacklisted" in msg or "blocked" in msg:
        return "sms_error_blocked"
    return "sms_error_twilio_rejected"
