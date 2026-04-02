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
def verify_email(request):
    """
    Verify a user's email using a code or token.
    """
    import urllib.parse

    serializer = EmailVerificationSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    user = serializer.validated_data["user"]
    code = serializer.validated_data.get("code")
    token = serializer.validated_data.get("token")

    # Check if email is already verified
    if user.email_verified:
        return success_response(message="Email is already verified.")

    # Build filters - try to find unverified verification records
    filters = {"user": user, "is_verified": False}

    if code:
        code_value = code.strip() if isinstance(code, str) else code
        if code_value:
            filters["code"] = code_value

    if token:
        # Handle URL decoding in case token was URL-encoded
        token_value = token.strip() if isinstance(token, str) else token
        if token_value:
            # Try URL decoding in case it was encoded
            try:
                token_value = urllib.parse.unquote(token_value)
            except Exception:
                pass
            filters["token"] = token_value

    # If neither code nor token provided, return error
    if not code and not token:
        return error_response(
            "Either code or token must be provided.",
            code="bad_request",
        )

    # Try to find the verification record
    verification = (
        EmailVerification.objects.filter(**filters).order_by("-created_at").first()
    )

    # If not found, check if there's a verification with this token/code that's already verified
    if not verification:
        # Try without is_verified filter to see if it exists but is already verified
        alt_filters = {"user": user}
        if code:
            alt_filters["code"] = code.strip() if isinstance(code, str) else code
        if token:
            token_value = token.strip() if isinstance(token, str) else token
            try:
                token_value = urllib.parse.unquote(token_value)
            except Exception:
                pass
            alt_filters["token"] = token_value

        existing = EmailVerification.objects.filter(**alt_filters).first()
        if existing and existing.is_verified:
            # Email is already verified via this token
            if not user.email_verified:
                user.email_verified = True
                user.save(update_fields=["email_verified"])
            return success_response(message="Email is already verified.")

        return error_response(
            "Invalid or expired verification code.",
            code="bad_request",
        )

    if verification.is_expired:
        verification.delete()
        return error_response(
            "Verification code has expired. Please request a new one.",
            code="bad_request",
        )

    # Mark as verified
    verification.mark_verified()
    user.email_verified = True
    user.save(update_fields=["email_verified"])

    return success_response(message="Email verified successfully.")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def resend_verification(request):
    """
    Resend email verification code to the user's email.
    """
    email = request.data.get("email")
    if not email:
        return error_response(
            "Email is required.",
            code="bad_request",
        )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return error_response(
            "User with this email does not exist.",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Check if email is already verified
    if user.email_verified:
        return success_response(message="Email is already verified.")

    # Check if user is requesting for their own email or is admin
    if request.user.email != email and not request.user.is_staff:
        return error_response(
            "You can only request verification for your own email.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        expiry_hours = getattr(settings, "EMAIL_VERIFICATION_EXPIRY_HOURS", 48)
        verification = EmailVerification.create_for_user(
            user, expiry_hours=expiry_hours
        )

        # Use user's chosen language, then Accept-Language header, then default
        language = get_email_language_for_user(user, request, default="en")
        sent = send_email_verification(user, verification, language=language)

        return success_response(
            data={
                "message": (
                    "Verification code sent successfully."
                    if sent
                    else "Failed to send verification email."
                ),
                "sent": sent,
                "expires_at": verification.expires_at.isoformat(),
            },
        )
    except Exception as exc:
        logger.error("Failed to resend verification email: %s", exc)
        return error_response(
            "Failed to send verification email. Please try again later.",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def check_registration_availability(request):
    """
    Check if company domain, email, username, or phone are available prior to registration.
    """
    serializer = RegistrationAvailabilitySerializer(data=request.data)
    if serializer.is_valid():
        return success_response(data={"available": True})

    return error_response(
        "Not available.",
        code="validation_error",
        details={"available": False, "errors": serializer.errors},
        status_code=status.HTTP_400_BAD_REQUEST,
    )

