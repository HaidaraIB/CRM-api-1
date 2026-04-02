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

from ..services import get_client_ip

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def impersonate(request):
    """
    Super admin only: obtain JWT tokens as a company owner (impersonation).
    POST /api/auth/impersonate/
    Body: { "user_id": <id> } or { "company_id": <id> }
    Response: { "access", "refresh", "user", "impersonated_by", "impersonation_code" (optional) }
    impersonation_code is a short-lived one-time code to exchange for tokens in the CRM app (GET /api/auth/impersonate-exchange/?code=...).
    """
    serializer = ImpersonateSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    target_user = serializer.validated_data["target_user"]
    company = serializer.validated_data.get("company")
    refresh = RefreshToken.for_user(target_user)
    user_payload = build_user_auth_payload(target_user, request)

    response_data = {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": user_payload,
        "impersonated_by": {
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
        },
    }

    # Audit log
    try:
        from settings.services import log_system_action
        log_system_action(
            action="impersonation_start",
            user=request.user,
            message=f"Super admin {request.user.email} impersonated {target_user.email} ({target_user.username})",
            metadata={
                "target_user_id": target_user.id,
                "target_username": target_user.username,
                "target_email": target_user.email,
                "company_id": company.id if company else None,
                "company_name": company.name if company else None,
            },
            ip_address=get_client_ip(request),
        )
    except Exception as e:
        logger.warning("Failed to write impersonation audit log: %s", e)

    # One-time code for CRM app handoff (120s TTL). Use DB so all workers see it (cache is often per-process).
    impersonation_code = secrets.token_urlsafe(32)
    payload = {
        "access": response_data["access"],
        "refresh": response_data["refresh"],
        "user": user_payload,
    }
    expires_at = timezone.now() + timedelta(seconds=120)
    ImpersonationSession.objects.create(
        code=impersonation_code,
        payload=payload,
        expires_at=expires_at,
    )
    # Also set in cache for single-worker / same-process hits
    cache.set(f"impersonate:{impersonation_code}", payload, timeout=120)
    response_data["impersonation_code"] = impersonation_code

    return success_response(data=response_data)


@api_view(["GET"])
@permission_classes([AllowAny])
def impersonate_exchange_status(request):
    """Diagnostic: GET /api/auth/impersonate-exchange/status/ returns 200 if this app revision is deployed."""
    return success_response(
        data={"status": "ok", "endpoint": "impersonate-exchange"},
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def impersonate_exchange(request):
    """
    Exchange a one-time impersonation code for tokens (used by CRM app after redirect).
    GET /api/auth/impersonate-exchange/?code=<impersonation_code>
    Returns: { "access", "refresh", "user" }. Code is invalidated after use.
    """
    logger.info("impersonate_exchange view called for path=%s", request.path)
    code = request.query_params.get("code", "").strip()
    if not code:
        return error_response("Missing code parameter.", code="missing_parameter")
    # Prefer DB (shared across workers); fallback to cache (same process)
    data = None
    now = timezone.now()
    session = ImpersonationSession.objects.filter(code=code).first()
    if session and session.expires_at > now:
        data = session.payload
        session.delete()
    if not data:
        data = cache.get(f"impersonate:{code}")
        if data:
            cache.delete(f"impersonate:{code}")
    if not data:
        logger.warning(
            "impersonate_exchange: code not found or expired (code=%s..., session_found=%s)",
            code[:12] if len(code) > 12 else code,
            bool(session),
        )
        # Clean up expired DB entries
        ImpersonationSession.objects.filter(expires_at__lte=now).delete()
        hint = None
        if settings.DEBUG and session:
            hint = "Code was found but expired (expires_at <= now)."
        elif settings.DEBUG:
            hint = "Code not in DB. Ensure dashboard and admin panel use the same API URL."
        return error_response(
            "Invalid or expired code.",
            code="invalid_or_expired_code",
            details={"hint": hint} if hint else None,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return success_response(data=data)
