import hashlib

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner  # noqa: F401


def hash_otp_code(phone_normalized: str, code: str) -> str:
    pepper = (getattr(settings, "PLATFORM_WHATSAPP_OTP_PEPPER", "") or "").strip() or settings.SECRET_KEY
    raw = f"{phone_normalized}:{code}:{pepper}".encode()
    return hashlib.sha256(raw).hexdigest()


def sign_phone_registration_token(phone_normalized: str) -> str:
    signer = TimestampSigner(salt="phone-registration-v1")
    return signer.sign(phone_normalized)


def unsign_phone_registration_token(token: str, max_age: int = 1800) -> str:
    signer = TimestampSigner(salt="phone-registration-v1")
    return signer.unsign(token, max_age=max_age)


__all__ = (
    "hash_otp_code",
    "sign_phone_registration_token",
    "unsign_phone_registration_token",
    "BadSignature",
    "SignatureExpired",
)
