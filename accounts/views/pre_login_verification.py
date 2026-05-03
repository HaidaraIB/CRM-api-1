"""
Pre-login email/phone verification helpers for company owners (password-gated, AllowAny).
Used by the web/mobile verify flows when JWT login is blocked on missing verification.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny

from crm_saas_api.responses import error_response, success_response, validation_error_response
from crm_saas_api.throttles import AuthRateThrottle

from ..models import EmailVerification, User
from ..phone_otp_policy import (
    CHANNEL_TWILIO_SMS,
    CHANNEL_WHATSAPP,
    effective_phone_otp_channel,
    effective_phone_otp_required,
    platform_twilio_ready_for_registration_otp,
)
from ..phone_otp_utils import hash_otp_code
from ..platform_registration_sms import send_registration_otp_sms
from ..platform_whatsapp import normalize_phone_digits, platform_whatsapp_configured, send_otp_template
from ..two_factor_policy import is_company_owner
from ..utils import get_email_language_for_user, send_email_verification
from integrations.services.twilio_phone import normalize_phone_to_e164

logger = logging.getLogger(__name__)

OWNER_PRELOGIN_OTP_CACHE = "owner_prelogin_otp:"
OTP_EXPIRE_MINUTES = 10


def _authenticate_user(username: str, password: str):
    username = (username or "").strip()
    if not username or not password:
        return None
    user = None
    if "@" in username:
        user = User.objects.filter(email__iexact=username).first()
    else:
        user = User.objects.filter(username__iexact=username).first()
    if not user or not user.check_password(password):
        return None
    return user


def _phone_to_e164(phone_raw: str, digits_only: str) -> str:
    raw = (phone_raw or "").strip()
    if raw.startswith("+"):
        return normalize_phone_to_e164(raw)
    return normalize_phone_to_e164("+" + digits_only)


def _owner_prelogin_cache_key(user_id: int) -> str:
    return f"{OWNER_PRELOGIN_OTP_CACHE}{user_id}"


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def pre_login_email_resend(request):
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    user = _authenticate_user(username, password)
    if not user:
        return error_response(
            "Invalid credentials.",
            code="authentication_failed",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not is_company_owner(user):
        return error_response("Forbidden.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    if user.email_verified:
        return success_response(message="Email is already verified.")

    try:
        expiry_hours = getattr(settings, "EMAIL_VERIFICATION_EXPIRY_HOURS", 48)
        verification = EmailVerification.create_for_user(user, expiry_hours=expiry_hours)
        language = get_email_language_for_user(user, request, default="en")
        sent = send_email_verification(user, verification, language=language)
        return success_response(
            data={
                "sent": bool(sent),
                "expires_at": verification.expires_at.isoformat(),
            },
            message="Verification email sent." if sent else "Failed to send email.",
        )
    except Exception as exc:
        logger.exception("pre_login_email_resend failed: %s", exc)
        return error_response(
            "Failed to send verification email.",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def pre_login_email_change(request):
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    new_email = (request.data.get("new_email") or "").strip().lower()
    if not new_email:
        return validation_error_response({"new_email": ["This field is required."]})

    user = _authenticate_user(username, password)
    if not user:
        return error_response(
            "Invalid credentials.",
            code="authentication_failed",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not is_company_owner(user):
        return error_response("Forbidden.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    if user.email_verified:
        return error_response(
            "Email is already verified; change it from your profile after signing in.",
            code="bad_request",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if User.objects.exclude(pk=user.pk).filter(email__iexact=new_email).exists():
        return error_response("This email is already in use.", code="email_taken", status_code=status.HTTP_400_BAD_REQUEST)

    user.email = new_email
    user.save(update_fields=["email"])
    EmailVerification.objects.filter(user=user, is_verified=False).delete()

    try:
        expiry_hours = getattr(settings, "EMAIL_VERIFICATION_EXPIRY_HOURS", 48)
        verification = EmailVerification.create_for_user(user, expiry_hours=expiry_hours)
        language = get_email_language_for_user(user, request, default="en")
        send_email_verification(user, verification, language=language)
        return success_response(
            data={"expires_at": verification.expires_at.isoformat()},
            message="Email updated. A verification code was sent to the new address.",
        )
    except Exception as exc:
        logger.exception("pre_login_email_change failed: %s", exc)
        return error_response(
            "Failed to send verification email.",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def pre_login_phone_send_otp(request):
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    user = _authenticate_user(username, password)
    if not user:
        return error_response(
            "Invalid credentials.",
            code="authentication_failed",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not is_company_owner(user):
        return error_response("Forbidden.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    if user.phone_verified:
        return success_response(message="Phone is already verified.")
    if not effective_phone_otp_required():
        return error_response(
            "Phone verification is not required.",
            code="registration_otp_disabled",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    phone_raw = (user.phone or "").strip()
    phone = normalize_phone_digits(phone_raw)
    if not phone or len(phone) < 8:
        return error_response("No valid phone on file.", code="bad_request", status_code=status.HTTP_400_BAD_REQUEST)

    channel = effective_phone_otp_channel()
    if not channel:
        return error_response(
            "Phone OTP is not configured.",
            code="phone_otp_misconfigured",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if channel == CHANNEL_WHATSAPP and not platform_whatsapp_configured():
        return error_response(
            "WhatsApp verification is not configured.",
            code="whatsapp_otp_not_configured",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if channel == CHANNEL_TWILIO_SMS and not platform_twilio_ready_for_registration_otp():
        return error_response(
            "SMS verification is not configured.",
            code="twilio_otp_not_configured",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    code = f"{secrets.randbelow(900000) + 100000}"
    code_hash = hash_otp_code(phone, code)
    cache.set(
        _owner_prelogin_cache_key(user.id),
        {"h": code_hash, "phone": phone},
        timeout=OTP_EXPIRE_MINUTES * 60,
    )

    if channel == CHANNEL_WHATSAPP:
        ok, details = send_otp_template(phone, code)
        if not ok:
            cache.delete(_owner_prelogin_cache_key(user.id))
            logger.warning("pre_login phone WhatsApp send failed: %s", details)
            return error_response(
                "Could not send verification code via WhatsApp.",
                code="whatsapp_send_failed",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
    else:
        to_e164 = _phone_to_e164(phone_raw, phone)
        ok, details = send_registration_otp_sms(to_e164, code, OTP_EXPIRE_MINUTES)
        if not ok:
            cache.delete(_owner_prelogin_cache_key(user.id))
            logger.warning("pre_login phone SMS send failed: %s", details)
            return error_response(
                "Could not send verification code via SMS.",
                code="twilio_send_failed",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

    return success_response(
        data={
            "expires_in_seconds": OTP_EXPIRE_MINUTES * 60,
            "channel": channel,
        },
        message="Verification code sent.",
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def pre_login_phone_verify_otp(request):
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    code = (request.data.get("code") or "").strip().replace(" ", "")
    user = _authenticate_user(username, password)
    if not user:
        return error_response(
            "Invalid credentials.",
            code="authentication_failed",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not is_company_owner(user):
        return error_response("Forbidden.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    if user.phone_verified:
        return success_response(message="Phone is already verified.")

    if not code.isdigit() or len(code) < 4:
        return validation_error_response({"code": ["Enter the verification code."]})

    key = _owner_prelogin_cache_key(user.id)
    cached = cache.get(key)
    phone = normalize_phone_digits((user.phone or "").strip())
    if not cached or cached.get("phone") != phone:
        return error_response(
            "No verification request found. Request a new code.",
            code="otp_not_found",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if hash_otp_code(phone, code) != cached.get("h"):
        return error_response(
            "Invalid verification code.",
            code="otp_invalid",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cache.delete(key)
    user.phone_verified = True
    user.save(update_fields=["phone_verified"])
    return success_response(message="Phone verified successfully.")


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def pre_login_phone_change(request):
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    new_phone_raw = (request.data.get("new_phone") or "").strip()
    user = _authenticate_user(username, password)
    if not user:
        return error_response(
            "Invalid credentials.",
            code="authentication_failed",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not is_company_owner(user):
        return error_response("Forbidden.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    if user.phone_verified:
        return error_response(
            "Phone is already verified; change it from your profile after signing in.",
            code="bad_request",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    new_phone = normalize_phone_digits(new_phone_raw)
    if not new_phone or len(new_phone) < 8:
        return validation_error_response({"new_phone": ["Invalid phone number."]})
    if User.objects.exclude(pk=user.pk).filter(phone=new_phone).exists():
        return error_response(
            "This phone number is already in use.",
            code="phone_taken",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cache.delete(_owner_prelogin_cache_key(user.id))
    user.phone = new_phone
    user.save(update_fields=["phone"])
    return success_response(message="Phone number updated. Request a new verification code.")
