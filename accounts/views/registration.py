from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes as throttle_decorator
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView
from crm_saas_api.responses import error_response, success_response, validation_error_response
from crm_saas_api.throttles import AuthRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from ..models import User, Role, EmailVerification, PasswordReset, TwoFactorAuth, LimitedAdmin, SupervisorPermission, ImpersonationSession
from ..serializers import (
    UserSerializer,
    UserListSerializer,
    CustomTokenObtainPairSerializer,
    ChangePasswordSerializer,
    RegisterCompanySerializer,
    EmailVerificationSerializer,
    RegistrationAvailabilitySerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
    RequestTwoFactorAuthSerializer,
    VerifyTwoFactorAuthSerializer,
    LimitedAdminSerializer,
    CreateLimitedAdminSerializer,
    SupervisorSerializer,
    CreateSupervisorSerializer,
    ImpersonateSerializer,
    build_user_auth_payload,
)
from ..permissions import CanAccessUser, CanManageLimitedAdmins, CanManageSupervisors, HasActiveSubscription, IsSuperAdmin
from ..phone_otp_policy import (
    PHONE_OTP_CHANNEL_CACHE_KEY,
    PHONE_OTP_REQUIRED_CACHE_KEY,
    VALID_CHANNELS,
    channel_is_configured,
    effective_phone_otp_channel,
    effective_phone_otp_required,
)
from ..email_registration_policy import (
    EMAIL_VERIFICATION_REQUIRED_CACHE_KEY,
    effective_registration_email_verification_required,
)
from companies.models import Company
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
import secrets
from ..utils import (
    get_email_language_for_user,
    send_email_verification,
    send_password_reset_email,
    send_two_factor_auth_email,
)
import logging

logger = logging.getLogger(__name__)


def _request_can_manage_settings(request) -> bool:
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False) or user.is_super_admin():
        return True
    try:
        la = user.limited_admin_profile
        return bool(la.is_active and la.can_manage_settings)
    except Exception:
        return False


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_decorator([AuthRateThrottle])
def register_company(request):
    """
    Register a new company with owner
    POST /api/auth/register/
    Body: {
        company: { name, domain, specialization },
        owner: { first_name, last_name, email, username, password },
        plan_id?: number,
        billing_cycle?: 'monthly' | 'yearly'
    }
    Response: { access, refresh, user, company, subscription? }
    """
    serializer = RegisterCompanySerializer(data=request.data, context={"request": request})

    if serializer.is_valid():
        result = serializer.save()
        company = result["company"]
        owner = result["owner"]
        subscription = result.get("subscription")
        requires_payment = result.get("requires_payment", False)

        # Generate JWT tokens
        refresh = RefreshToken.for_user(owner)

        # Prepare response data
        response_data = {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {
                "id": owner.id,
                "username": owner.username,
                "email": owner.email,
                "first_name": owner.first_name,
                "last_name": owner.last_name,
                "phone": owner.phone or "",
                "profile_photo": (
                    request.build_absolute_uri(owner.profile_photo.url)
                    if owner.profile_photo
                    else None
                ),
                "role": owner.role,
                "email_verified": owner.email_verified,
                "phone_verified": getattr(owner, "phone_verified", False),
                "company": company.id,
                "company_name": company.name,
                "company_specialization": company.specialization,
                "language": getattr(owner, "language", None) or "ar",
            },
            "company": {
                "id": company.id,
                "name": company.name,
                "domain": company.domain,
                "specialization": company.specialization,
            },
            "requires_payment": requires_payment,
        }

        if subscription:
            response_data["subscription"] = {
                "id": subscription.id,
                "plan_id": subscription.plan.id,
                "plan_name": subscription.plan.name,
                "is_active": subscription.is_active,
                "end_date": subscription.end_date.isoformat(),
            }

        # Email verification is now handled on-demand via the resend-verification endpoint
        # Don't send verification email automatically during registration
        verification_info = {}
        try:
            expiry_hours = getattr(settings, "EMAIL_VERIFICATION_EXPIRY_HOURS", 48)
            verification = EmailVerification.create_for_user(
                owner, expiry_hours=expiry_hours
            )
            # Don't send email automatically - user will request it from the modal
            verification_info = {
                "sent": False,
                "expires_at": verification.expires_at.isoformat(),
            }
        except Exception as exc:
            logger.warning("Unable to create verification: %s", exc)
            verification_info = {
                "sent": False,
                "error": str(exc),
            }

        response_data["email_verification"] = verification_info

        return success_response(data=response_data, status_code=status.HTTP_201_CREATED)

    return validation_error_response(serializer.errors)


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def phone_otp_requirement_settings(request):
    if request.method == "GET":
        req = effective_phone_otp_required()
        ch = effective_phone_otp_channel() if req else None
        return success_response(
            data={
                "phone_otp_required": req,
                "phone_otp_channel": ch,
            }
        )

    if not _request_can_manage_settings(request):
        return error_response(
            "Permission denied",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    value = request.data.get("phone_otp_required", None)
    if value is None:
        return validation_error_response({"phone_otp_required": ["This field is required."]})
    normalized_req = value
    if isinstance(value, str):
        normalized_req = value.strip().lower() in ("1", "true", "yes", "on")
    normalized_req = bool(normalized_req)

    if not normalized_req:
        cache.set(PHONE_OTP_REQUIRED_CACHE_KEY, False, timeout=None)
        cache.delete(PHONE_OTP_CHANNEL_CACHE_KEY)
        return success_response(
            data={"phone_otp_required": False, "phone_otp_channel": None}
        )

    raw_ch = request.data.get("phone_otp_channel")
    ch = (raw_ch if isinstance(raw_ch, str) else "") or ""
    ch = ch.strip().lower()
    if ch not in VALID_CHANNELS:
        return validation_error_response(
            {
                "phone_otp_channel": [
                    "Select whatsapp or twilio_sms when phone OTP is required."
                ]
            }
        )
    if not channel_is_configured(ch):
        code = "whatsapp_otp_not_configured" if ch == "whatsapp" else "twilio_otp_not_configured"
        return error_response(
            "Selected channel is not configured.",
            code=code,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cache.set(PHONE_OTP_REQUIRED_CACHE_KEY, True, timeout=None)
    cache.set(PHONE_OTP_CHANNEL_CACHE_KEY, ch, timeout=None)
    return success_response(
        data={"phone_otp_required": True, "phone_otp_channel": ch}
    )


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def registration_email_requirement_settings(request):
    if request.method == "GET":
        return success_response(
            data={
                "email_verification_required": effective_registration_email_verification_required(),
            }
        )

    if not _request_can_manage_settings(request):
        return error_response(
            "Permission denied",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    value = request.data.get("email_verification_required", None)
    if value is None:
        return validation_error_response({"email_verification_required": ["This field is required."]})
    normalized_req = value
    if isinstance(value, str):
        normalized_req = value.strip().lower() in ("1", "true", "yes", "on")
    normalized_req = bool(normalized_req)

    cache.set(EMAIL_VERIFICATION_REQUIRED_CACHE_KEY, normalized_req, timeout=None)
    return success_response(
        data={"email_verification_required": normalized_req}
    )


