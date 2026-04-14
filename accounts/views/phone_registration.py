import logging
import secrets
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny

from crm_saas_api.responses import error_response, success_response, validation_error_response
from crm_saas_api.throttles import AuthRateThrottle

from ..models import PhoneRegistrationChallenge, User
from ..phone_otp_utils import hash_otp_code, sign_phone_registration_token
from ..platform_whatsapp import normalize_phone_digits, platform_whatsapp_configured, send_otp_template

logger = logging.getLogger(__name__)

OTP_EXPIRE_MINUTES = 10
MAX_ATTEMPTS = 5
PHONE_SEND_CACHE_PREFIX = "phone_otp_send:"


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or ""


def _rate_allow_phone_send(phone: str) -> bool:
    key = f"{PHONE_SEND_CACHE_PREFIX}{phone}"
    n = cache.get(key) or 0
    if n >= 5:
        return False
    cache.set(key, n + 1, 3600)
    return True


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def register_phone_send_otp(request):
    """
    POST /api/auth/register/phone/send-otp/
    Body: { "phone": "+966..." }
    """
    phone_raw = (request.data.get("phone") or "").strip()
    phone = normalize_phone_digits(phone_raw)
    if not phone or len(phone) < 8:
        return validation_error_response({"phone": ["Invalid phone number."]})

    if User.objects.filter(phone=phone).exists():
        return error_response(
            "This phone number is already registered.",
            code="phone_already_registered",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not platform_whatsapp_configured():
        return error_response(
            "WhatsApp verification is not configured.",
            code="whatsapp_otp_not_configured",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if not _rate_allow_phone_send(phone):
        return error_response(
            "Too many verification attempts for this number. Try again later.",
            code="otp_rate_limited",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    code = f"{secrets.randbelow(900000) + 100000}"
    code_hash = hash_otp_code(phone, code)
    expires_at = timezone.now() + timedelta(minutes=OTP_EXPIRE_MINUTES)

    PhoneRegistrationChallenge.objects.filter(
        phone_normalized=phone, consumed_at__isnull=True
    ).delete()

    PhoneRegistrationChallenge.objects.create(
        phone_normalized=phone,
        code_hash=code_hash,
        expires_at=expires_at,
    )

    ok, details = send_otp_template(phone, code)
    if not ok:
        logger.warning("OTP WhatsApp send failed: phone=%s details=%s", phone[-4:], details)
        return error_response(
            "Could not send verification code via WhatsApp.",
            code="whatsapp_send_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
            details=details if isinstance(details, dict) else {"error": str(details)},
        )

    return success_response(
        data={
            "expires_in_seconds": OTP_EXPIRE_MINUTES * 60,
            "phone": phone,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([AuthRateThrottle])
def register_phone_verify_otp(request):
    """
    POST /api/auth/register/phone/verify-otp/
    Body: { "phone": "+...", "code": "123456" }
    """
    phone_raw = (request.data.get("phone") or "").strip()
    code = (request.data.get("code") or "").strip().replace(" ", "")
    phone = normalize_phone_digits(phone_raw)
    if not phone or not code.isdigit() or len(code) < 4:
        return validation_error_response({"code": ["Enter the verification code."]})

    challenge = (
        PhoneRegistrationChallenge.objects.filter(phone_normalized=phone, consumed_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not challenge:
        return error_response(
            "No verification request found. Request a new code.",
            code="otp_not_found",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if challenge.expires_at < timezone.now():
        return error_response(
            "Verification code expired. Request a new code.",
            code="otp_expired",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if challenge.attempts >= MAX_ATTEMPTS:
        return error_response(
            "Too many incorrect attempts. Request a new code.",
            code="otp_locked",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    challenge.attempts += 1
    challenge.save(update_fields=["attempts"])

    if hash_otp_code(phone, code) != challenge.code_hash:
        return error_response(
            "Invalid verification code.",
            code="otp_invalid",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["consumed_at"])

    token = sign_phone_registration_token(phone)
    return success_response(
        data={
            "phone_verification_token": token,
            "expires_in_seconds": 1800,
        }
    )
