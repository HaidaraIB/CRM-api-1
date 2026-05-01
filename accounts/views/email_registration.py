import secrets

from django.core.cache import cache
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny

from crm_saas_api.responses import error_response, success_response, validation_error_response
from crm_saas_api.throttles import AuthRateThrottle

from accounts.email_registration_policy import effective_registration_email_verification_required
from accounts.email_registration_utils import (
    hash_email_otp,
    sign_email_registration_token,
)
from accounts.models import User
from accounts.platform_registration_email import send_registration_otp_email

EMAIL_SEND_CACHE_PREFIX = "registration_email_otp_send:"
EMAIL_CHALLENGE_CACHE_PREFIX = "registration_email_otp_challenge:"
EMAIL_OTP_EXPIRE_MINUTES = 10
EMAIL_OTP_MAX_ATTEMPTS = 5


def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _rate_allow_email_send(email: str) -> bool:
    key = f"{EMAIL_SEND_CACHE_PREFIX}{email}"
    n = cache.get(key) or 0
    if n >= 5:
        return False
    cache.set(key, n + 1, 3600)
    return True


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def register_email_send_otp(request):
    if not effective_registration_email_verification_required():
        return error_response(
            "Email verification is not required for registration.",
            code="registration_email_verification_disabled",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    email = _normalize_email(request.data.get("email"))
    if not email:
        return validation_error_response({"email": ["Email is required."]})
    try:
        validate_email(email)
    except ValidationError:
        return validation_error_response({"email": ["Invalid email address."]})

    if User.objects.filter(email__iexact=email).exists():
        return error_response(
            "This email is already registered.",
            code="email_already_registered",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not _rate_allow_email_send(email):
        return error_response(
            "Too many verification attempts for this email. Try again later.",
            code="email_otp_rate_limited",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    code = f"{secrets.randbelow(900000) + 100000}"
    challenge_key = f"{EMAIL_CHALLENGE_CACHE_PREFIX}{email}"
    payload = {
        "code_hash": hash_email_otp(email, code),
        "attempts": 0,
    }
    cache.set(challenge_key, payload, timeout=EMAIL_OTP_EXPIRE_MINUTES * 60)

    if not send_registration_otp_email(email, code, EMAIL_OTP_EXPIRE_MINUTES):
        return error_response(
            "Could not send email verification code.",
            code="email_otp_send_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    return success_response(
        data={
            "email": email,
            "expires_in_seconds": EMAIL_OTP_EXPIRE_MINUTES * 60,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def register_email_verify_otp(request):
    email = _normalize_email(request.data.get("email"))
    code = (request.data.get("code") or "").strip().replace(" ", "")
    if not email:
        return validation_error_response({"email": ["Email is required."]})
    if not code.isdigit() or len(code) < 4:
        return validation_error_response({"code": ["Enter the verification code."]})

    challenge_key = f"{EMAIL_CHALLENGE_CACHE_PREFIX}{email}"
    challenge = cache.get(challenge_key)
    if not challenge:
        return error_response(
            "No email verification request found. Request a new code.",
            code="email_otp_not_found",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    attempts = int(challenge.get("attempts", 0))
    if attempts >= EMAIL_OTP_MAX_ATTEMPTS:
        cache.delete(challenge_key)
        return error_response(
            "Too many invalid attempts. Request a new code.",
            code="email_otp_attempts_exceeded",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    expected_hash = challenge.get("code_hash")
    submitted_hash = hash_email_otp(email, code)
    if not expected_hash or submitted_hash != expected_hash:
        challenge["attempts"] = attempts + 1
        cache.set(challenge_key, challenge, timeout=EMAIL_OTP_EXPIRE_MINUTES * 60)
        return error_response(
            "Invalid verification code.",
            code="email_otp_invalid",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cache.delete(challenge_key)
    token = sign_email_registration_token(email)
    return success_response(
        data={
            "email_verification_token": token,
            "expires_in_seconds": 1800,
        }
    )
