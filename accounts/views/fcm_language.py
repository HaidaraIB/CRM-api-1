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
@permission_classes([IsAuthenticated])
def update_fcm_token(request):
    """
    Update FCM token and language for the authenticated user
    """
    user = request.user
    ip = (
        (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or ""
    )
    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:200]

    fcm_token = request.data.get("fcm_token", "").strip()
    language = request.data.get("language", "").strip()

    if not fcm_token:
        # Keep a WARNING here so it shows in django_important.log (filter keeps "invalid"+"key")
        logger.warning(
            "FCM update endpoint called with invalid key: fcm_token missing/empty "
            f"user_id={getattr(user, 'id', None)} username={getattr(user, 'username', None)} "
            f"ip={ip} ua={user_agent}"
        )
        return error_response(
            "fcm_token is required",
            code="bad_request",
        )

    token_len = len(fcm_token)
    token_prefix = fcm_token[:12]
    logger.info(
        "FCM update endpoint hit "
        f"user_id={getattr(user, 'id', None)} username={getattr(user, 'username', None)} "
        f"ip={ip} ua={user_agent} token_len={token_len} token_prefix={token_prefix}"
    )

    user.fcm_token = fcm_token

    # Update language if provided
    if language in ["ar", "en"]:
        user.language = language

    try:
        user.save(update_fields=["fcm_token", "language"])
    except Exception as e:
        # This will show in django_important.log (ERROR level).
        logger.exception(
            "FCM token update failed "
            f"user_id={getattr(user, 'id', None)} username={getattr(user, 'username', None)} "
            f"ip={ip} token_len={token_len} err={e}"
        )
        return error_response(
            "Failed to update fcm_token",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    logger.info(
        "FCM token updated successfully "
        f"user_id={getattr(user, 'id', None)} username={getattr(user, 'username', None)} "
        f"ip={ip} token_len={token_len} token_prefix={token_prefix} "
        f"language={(language if language else '-')}"
    )

    return success_response(message="FCM token updated successfully")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def fcm_diagnostics_full(request):
    """
    Receive full FCM diagnostic report (all steps) from the app.
    Logs each step to django.log with [FCM_DIAG_FULL] for easy grep.
    """
    user = request.user
    ip = (
        (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or ""
    )
    body = getattr(request, "data", None) or {}
    platform = body.get("platform") or "unknown"
    app_version = body.get("app_version") or ""
    steps = body.get("steps") or []

    logger.info(
        "[FCM_DIAG_FULL] START user_id=%s username=%s ip=%s platform=%s app_version=%s steps_count=%s",
        getattr(user, "id", None),
        getattr(user, "username", None),
        ip,
        platform,
        app_version,
        len(steps),
    )
    for i, step in enumerate(steps):
        step_id = step.get("step_id") or str(i)
        step_name = step.get("step_name") or "unknown"
        success = step.get("success")
        message = (step.get("message") or "")[:500]
        detail = (step.get("detail") or "")[:500]
        logger.info(
            "[FCM_DIAG_FULL] STEP %s | id=%s name=%s success=%s message=%s detail=%s",
            i + 1,
            step_id,
            step_name,
            success,
            message,
            detail,
        )
    logger.info(
        "[FCM_DIAG_FULL] END user_id=%s username=%s",
        getattr(user, "id", None),
        getattr(user, "username", None),
    )
    return success_response(
        data={
            "message": "FCM full diagnostics received",
            "steps_count": len(steps),
        },
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def update_language(request):
    """
    Update language preference for the authenticated user
    """
    language = request.data.get("language", "").strip()

    if not language:
        return error_response(
            "language is required",
            code="bad_request",
        )

    if language not in ["ar", "en"]:
        return error_response(
            'language must be either "ar" or "en"',
            code="bad_request",
        )

    user = request.user
    user.language = language
    user.save(update_fields=["language"])

    logger.info(f"Language updated to {language} for user {user.username}")

    return success_response(
        data={"message": "Language updated successfully", "language": language},
    )

