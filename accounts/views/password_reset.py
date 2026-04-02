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

@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_decorator([AuthRateThrottle])
def forgot_password(request):
    """
    Request password reset - sends email with reset code and link
    POST /api/auth/forgot-password/
    Body: { email: string }
    Response: { message: string, sent: bool }
    """
    serializer = ForgotPasswordSerializer(data=request.data)

    if not serializer.is_valid():
        # Don't reveal if email exists or not
        return success_response(
            message="If the email exists, a password reset link has been sent.",
        )

    user = serializer.validated_data.get("user")
    if not user:
        # Don't reveal if email exists or not
        return success_response(
            message="If the email exists, a password reset link has been sent.",
        )

    # Create password reset token
    try:
        expiry_hours = getattr(settings, "PASSWORD_RESET_EXPIRY_HOURS", 1)
        reset = PasswordReset.create_for_user(user, expiry_hours=expiry_hours)
        # Use user's chosen language, then Accept-Language header, then default
        language = get_email_language_for_user(user, request, default="en")
        sent = send_password_reset_email(user, reset, language=language)

        return success_response(
            data={
                "message": "If the email exists, a password reset link has been sent.",
                "sent": sent,
            },
        )
    except Exception as exc:
        logger.warning("Unable to send password reset email: %s", exc)
        # Don't reveal error to user
        return success_response(
            data={
                "message": "If the email exists, a password reset link has been sent.",
                "sent": False,
            },
        )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_decorator([AuthRateThrottle])
def reset_password(request):
    """
    Reset password using code or token
    POST /api/auth/reset-password/
    Body: { email: string, code?: string, token?: string, new_password: string, confirm_password: string }
    Response: { message: string }
    """
    import urllib.parse

    serializer = ResetPasswordSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    user = serializer.validated_data["user"]
    code = serializer.validated_data.get("code")
    token = serializer.validated_data.get("token")
    new_password = serializer.validated_data["new_password"]

    # Build filters
    filters = {"user": user, "is_used": False}

    if code:
        code_value = code.strip() if isinstance(code, str) else code
        if code_value:
            filters["code"] = code_value

    if token:
        token_value = token.strip() if isinstance(token, str) else token
        if token_value:
            try:
                token_value = urllib.parse.unquote(token_value)
            except Exception:
                pass
            filters["token"] = token_value

    if not code and not token:
        return error_response(
            "Either code or token must be provided.",
            code="bad_request",
        )

    # Find the reset record
    reset = PasswordReset.objects.filter(**filters).order_by("-created_at").first()

    if not reset:
        return error_response(
            "Invalid or expired reset code.",
            code="bad_request",
        )

    if reset.is_expired:
        reset.delete()
        return error_response(
            "Reset code has expired. Please request a new one.",
            code="bad_request",
        )

    if reset.is_used:
        return error_response(
            "This reset code has already been used.",
            code="bad_request",
        )

    # Reset password
    user.set_password(new_password)
    user.save()

    # Mark reset as used
    reset.mark_used()

    return success_response(message="Password has been reset successfully.")
