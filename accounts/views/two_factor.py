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
from ..two_factor_policy import (
    OWNER_TRUST_COOKIE_NAME,
    OWNER_TRUST_DAYS,
    is_company_owner,
    issue_trusted_device,
)
import logging

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_decorator([AuthRateThrottle])
def request_two_factor_auth(request):
    """
    Request 2FA code - sends email with 2FA code
    POST /api/auth/request-2fa/
    Body: { username: string, password: string }
    Response: { message: string, sent: bool, token: string }
    """
    serializer = RequestTwoFactorAuthSerializer(data=request.data)

    if not serializer.is_valid():
        # Normalize error format - extract error message from serializer errors
        error_message = None
        if "error" in serializer.errors:
            error_value = serializer.errors["error"]
            if isinstance(error_value, list) and len(error_value) > 0:
                error_message = error_value[0]
            elif isinstance(error_value, str):
                error_message = error_value
        elif "username" in serializer.errors:
            error_value = serializer.errors["username"]
            if isinstance(error_value, list) and len(error_value) > 0:
                error_message = error_value[0]
            elif isinstance(error_value, str):
                error_message = error_value

        if error_message:
            return error_response(error_message, code="validation_error")
        return validation_error_response(serializer.errors)

    user = serializer.validated_data.get("user")
    if not user:
        return error_response("User not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)

    is_owner = is_company_owner(user)
    if not is_owner:
        # Temporary backward compatibility for older mobile apps:
        # old clients still call /auth/request-2fa/ for all roles.
        logger.info(
            "Legacy /auth/request-2fa compatibility path for non-owner user_id=%s role=%s",
            user.id,
            user.role,
        )

    # Check subscription before sending 2FA code (for all users except Super Admin)
    if not user.is_super_admin():
        if user.company:
            from subscriptions.models import Subscription

            has_active_subscription = Subscription.objects.filter(
                company=user.company, is_active=True
            ).exists()

            if not has_active_subscription:
                # Return different error messages for admin vs employee
                subscription = (
                    Subscription.objects.filter(company=user.company)
                    .order_by("-created_at")
                    .first()
                )

                # Check user role - use role field directly to be sure
                is_employee_user = user.role in ("employee", "data_entry")

                if is_employee_user:
                    return error_response(
                        "Your account is temporarily inactive",
                        code="account_temporarily_inactive",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )
                details = {}
                if subscription:
                    details["subscriptionId"] = subscription.id
                return error_response(
                    "Your subscription is not active. Please contact support or Complete Your Payment to access the system.",
                    code="subscription_inactive",
                    details=details or None,
                    status_code=status.HTTP_403_FORBIDDEN,
                )

    # Create 2FA code
    try:
        expiry_minutes = 10  # 2FA codes expire in 10 minutes

        # For demo accounts (Google/Apple store review), use constant 2FA from .env
        demo_2fa_code = None
        is_google_demo = (
            settings.DEMO_GOOGLE_ACCOUNT_USERNAME
            and user.username.lower() == settings.DEMO_GOOGLE_ACCOUNT_USERNAME.lower()
        ) or (
            settings.DEMO_GOOGLE_ACCOUNT_EMAIL
            and user.email.lower() == settings.DEMO_GOOGLE_ACCOUNT_EMAIL.lower()
        )
        is_apple_demo = (
            settings.DEMO_APPLE_ACCOUNT_USERNAME
            and user.username.lower() == settings.DEMO_APPLE_ACCOUNT_USERNAME.lower()
        ) or (
            settings.DEMO_APPLE_ACCOUNT_EMAIL
            and user.email.lower() == settings.DEMO_APPLE_ACCOUNT_EMAIL.lower()
        )
        if is_google_demo and getattr(settings, "DEMO_GOOGLE_ACCOUNT_2FA_CODE", ""):
            demo_2fa_code = settings.DEMO_GOOGLE_ACCOUNT_2FA_CODE
        elif is_apple_demo and getattr(settings, "DEMO_APPLE_ACCOUNT_2FA_CODE", ""):
            demo_2fa_code = settings.DEMO_APPLE_ACCOUNT_2FA_CODE

        if demo_2fa_code:
            # Use constant code for demo account (Google or Apple)
            # Delete old unused 2FA codes for this user
            TwoFactorAuth.objects.filter(user=user, is_verified=False).delete()

            # Create 2FA with constant code
            import uuid
            from django.utils import timezone
            from datetime import timedelta

            token = uuid.uuid4().hex
            expires_at = timezone.now() + timedelta(minutes=expiry_minutes)
            two_fa = TwoFactorAuth.objects.create(
                user=user,
                code=demo_2fa_code,
                token=token,
                expires_at=expires_at,
            )
        else:
            # Normal flow: generate random code
            two_fa = TwoFactorAuth.create_for_user(user, expiry_minutes=expiry_minutes)

        # Use user's chosen language, then Accept-Language header, then default
        language = get_email_language_for_user(user, request, default="ar")
        if not is_apple_demo and not is_google_demo:
            sent = send_two_factor_auth_email(user, two_fa, language=language)
        else:
            sent = True
        return success_response(
            data={
                "message": "2FA code has been sent to your email.",
                "sent": sent,
                "token": two_fa.token,  # Return token for verification
            },
        )
    except Exception as exc:
        logger.warning("Unable to send 2FA email: %s", exc)
        return error_response(
            "Failed to send 2FA code. Please try again.",
            code="server_error",
            details={"sent": False},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def verify_two_factor_auth(request):
    """
    Verify 2FA code and return JWT tokens
    POST /api/auth/verify-2fa/
    Body: { username: string, code?: string, token?: string, password: string }
    Response: { access: string, refresh: string, user: {...} }
    """
    import urllib.parse

    # First verify password
    username_or_email = request.data.get("username", "").strip()
    password = request.data.get("password", "")

    if not username_or_email or not password:
        return error_response(
            "Username and password are required.",
            code="missing_credentials",
        )

    # Find user
    user = None
    if "@" in username_or_email:
        try:
            user = User.objects.get(email__iexact=username_or_email)
        except User.DoesNotExist:
            return error_response(
                "Invalid credentials.",
                code="authentication_failed",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
    else:
        try:
            user = User.objects.get(username__iexact=username_or_email)
        except User.DoesNotExist:
            return error_response(
                "Invalid credentials.",
                code="authentication_failed",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

    # Verify password
    if not user.check_password(password):
        return error_response(
            "Invalid credentials.",
            code="authentication_failed",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Now verify 2FA code
    serializer = VerifyTwoFactorAuthSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    code = serializer.validated_data.get("code")
    token = serializer.validated_data.get("token")

    # Build filters
    filters = {"user": user, "is_verified": False}

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
            code="missing_code_or_token",
        )

    # Find the 2FA record
    two_fa = TwoFactorAuth.objects.filter(**filters).order_by("-created_at").first()

    if not two_fa:
        return error_response(
            "Invalid or expired 2FA code.",
            code="invalid_two_factor_code",
        )

    if two_fa.is_expired:
        two_fa.delete()
        return error_response(
            "2FA code has expired. Please request a new one.",
            code="two_factor_expired",
        )

    if two_fa.is_verified:
        return error_response(
            "This 2FA code has already been used.",
            code="two_factor_already_used",
        )

    # Mark as verified
    two_fa.mark_verified()

    # Check subscription for all users except Super Admin
    # Super Admin doesn't need active subscription
    if not user.is_super_admin():
        from subscriptions.models import Subscription

        # Check if user has a company
        if not user.company:
            return error_response(
                "Your account is not associated with a company. Please contact support.",
                code="no_company",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        # Check if company has an active subscription
        has_active_subscription = Subscription.objects.filter(
            company=user.company, is_active=True
        ).exists()

        if not has_active_subscription:
            subscription = (
                Subscription.objects.filter(company=user.company)
                .order_by("-created_at")
                .first()
            )

            is_employee_user = user.role in ("employee", "data_entry")

            if is_employee_user:
                return error_response(
                    "Your account is temporarily inactive",
                    code="account_temporarily_inactive",
                    status_code=status.HTTP_403_FORBIDDEN,
                )
            details = {}
            if subscription:
                details["subscriptionId"] = subscription.id
            return error_response(
                "Your subscription is not active. Please contact support or Complete Your Payment to access the system.",
                code="subscription_inactive",
                details=details or None,
                status_code=status.HTTP_403_FORBIDDEN,
            )

    # Generate JWT tokens
    refresh = RefreshToken.for_user(user)

    # Prepare response data
    response_data = {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone or "",
            "profile_photo": (
                request.build_absolute_uri(user.profile_photo.url)
                if user.profile_photo
                else None
            ),
            "role": user.role,
            "email_verified": user.email_verified,
            "company": user.company.id if user.company else None,
            "company_name": user.company.name if user.company else None,
            "company_specialization": (
                user.company.specialization if user.company else None
            ),
            "language": getattr(user, "language", None) or "ar",
        },
    }

    response = success_response(data=response_data)

    trust_device = bool(request.data.get("trust_device", False))
    if is_company_owner(user) and trust_device:
        trusted_token, trusted_until = issue_trusted_device(user, request)
        # Mobile clients don't persist HttpOnly cookies reliably with plain HTTP
        # stacks, so also return token in response payload for secure app storage.
        response.data.setdefault("data", {})
        response.data["data"]["trusted_device_token"] = trusted_token
        response.data["data"]["trusted_until"] = trusted_until.isoformat()
        response.set_cookie(
            OWNER_TRUST_COOKIE_NAME,
            trusted_token,
            max_age=OWNER_TRUST_DAYS * 24 * 60 * 60,
            httponly=True,
            secure=not settings.DEBUG,
            samesite="Lax",
        )

    return response
