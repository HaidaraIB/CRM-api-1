import hashlib

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner  # noqa: F401


def hash_email_otp(email_normalized: str, code: str) -> str:
    pepper = (getattr(settings, "EMAIL_VERIFICATION_OTP_PEPPER", "") or "").strip() or settings.SECRET_KEY
    raw = f"{email_normalized}:{code}:{pepper}".encode()
    return hashlib.sha256(raw).hexdigest()


def sign_email_registration_token(email_normalized: str) -> str:
    signer = TimestampSigner(salt="email-registration-v1")
    return signer.sign(email_normalized)


def unsign_email_registration_token(token: str, max_age: int = 1800) -> str:
    signer = TimestampSigner(salt="email-registration-v1")
    return signer.unsign(token, max_age=max_age)


__all__ = (
    "hash_email_otp",
    "sign_email_registration_token",
    "unsign_email_registration_token",
    "BadSignature",
    "SignatureExpired",
)
