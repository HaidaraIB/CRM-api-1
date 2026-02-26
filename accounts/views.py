from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import User, Role, EmailVerification, PasswordReset, TwoFactorAuth, LimitedAdmin, SupervisorPermission
from .serializers import (
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
from .permissions import CanAccessUser, CanManageLimitedAdmins, CanManageSupervisors, HasActiveSubscription, IsSuperAdmin
from companies.models import Company
from django.conf import settings
from django.core.cache import cache
import secrets
from .utils import (
    send_email_verification,
    send_password_reset_email,
    send_two_factor_auth_email,
)
import logging

logger = logging.getLogger(__name__)


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Custom view to get a JWT token with user information
    """

    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing User instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = User.objects.all()
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanAccessUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name", "phone", "role"]
    ordering_fields = ["date_joined", "last_login", "username"]
    ordering = ["-date_joined"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        # Super Admin can access all users
        if user.is_super_admin():
            return queryset

        # Admin can access users in their company
        if user.is_admin() and user.company:
            return queryset.filter(company=user.company)

        # Supervisor with can_manage_users: full company users; can_manage_leads: list only (for Activities filter)
        if user.is_supervisor() and user.company:
            if user.supervisor_has_permission("manage_users"):
                return queryset.filter(company=user.company)
            if user.supervisor_has_permission("manage_leads"):
                return queryset.filter(company=user.company)

        # Employee can only access their own profile
        return queryset.filter(id=user.id)

    def perform_create(self, serializer):
        """Create user and automatically set company from request user, then link company owner if user is admin"""
        # Get company from request user (the user creating this new user)
        request_user = self.request.user
        company = (
            request_user.company if request_user and request_user.company else None
        )

        # Set company in the serializer's validated_data if not already set
        # Note: The serializer has 'company' as SerializerMethodField (read-only),
        # so we need to set it directly on the model instance after creation
        user = serializer.save()

        # Set company from request user if not already set
        if company and not user.company:
            user.company = company
            user.save(update_fields=["company"])

        # إذا كان المستخدم admin وله company، ربط Company.owner به تلقائياً
        if user.role == Role.ADMIN.value and user.company:
            # إذا لم يكن للـ company owner أو كان owner مختلف، ربط المستخدم كـ owner
            if not user.company.owner:
                user.company.owner = user
                user.company.save(update_fields=["owner"])
            elif user.company.owner != user:
                # إذا كان هناك owner آخر، يمكن اختيار استبداله أو عدم التحديث
                # هنا سنستبدله إذا كان المستخدم الجديد admin
                user.company.owner = user
                user.company.save(update_fields=["owner"])
        # When creating a supervisor, ensure they have a SupervisorPermission record (admin can then edit in Supervisors tab)
        if user.role == Role.SUPERVISOR.value:
            if not SupervisorPermission.objects.filter(user=user).exists():
                SupervisorPermission.objects.create(user=user, is_active=True)

    def perform_update(self, serializer):
        """Update user and handle company owner changes"""
        old_company = None
        old_role = None
        if self.get_object():
            old_company = self.get_object().company
            old_role = self.get_object().role

        user = serializer.save()
        new_company = user.company
        new_role = user.role

        # إذا كان المستخدم admin وله company، ربط Company.owner به تلقائياً
        if new_role == Role.ADMIN.value and new_company:
            # إذا تغيرت company أو role، تحديث Company.owner
            if new_company != old_company or new_role != old_role:
                # إزالة owner من company القديمة (إن وجدت)
                if (
                    old_company
                    and old_company != new_company
                    and old_company.owner == user
                ):
                    old_company.owner = None
                    old_company.save(update_fields=["owner"])

                # ربط Company.owner بالمستخدم الجديد
                if not new_company.owner or new_company.owner != user:
                    new_company.owner = user
                    new_company.save(update_fields=["owner"])
        elif old_company and old_company.owner == user:
            # إذا لم يعد المستخدم admin أو تمت إزالة company، إزالة owner من company
            old_company.owner = None
            old_company.save(update_fields=["owner"])
        if new_role == Role.SUPERVISOR.value and not SupervisorPermission.objects.filter(user=user).exists():
            SupervisorPermission.objects.create(user=user, is_active=True)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def get_serializer_class(self):
        if self.action == "list":
            return UserListSerializer
        return UserSerializer

    @action(
        detail=False, methods=["get"], permission_classes=[IsAuthenticated]
    )  # me endpoint doesn't require active subscription
    def me(self, request):
        serializer = UserSerializer(request.user, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def change_password(self, request):
        """
        Change password for the current authenticated user
        """
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )

        if serializer.is_valid():
            user = request.user
            current_password = serializer.validated_data["current_password"]
            new_password = serializer.validated_data["new_password"]

            # Verify current password
            if not user.check_password(current_password):
                return Response(
                    {"error": "Current password is incorrect."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate new password
            try:
                validate_password(new_password, user)
            except ValidationError as e:
                return Response(
                    {"error": " ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST
                )

            # Set new password
            user.set_password(new_password)
            user.save()

            return Response(
                {"message": "Password changed successfully."}, status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([AllowAny])
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
    serializer = RegisterCompanySerializer(data=request.data)

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
                "company": company.id,
                "company_name": company.name,
                "company_specialization": company.specialization,
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

        return Response(response_data, status=status.HTTP_201_CREATED)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def _get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


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
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
            ip_address=_get_client_ip(request),
        )
    except Exception as e:
        logger.warning("Failed to write impersonation audit log: %s", e)

    # One-time code for CRM app handoff (120s TTL)
    impersonation_code = secrets.token_urlsafe(32)
    cache_key = f"impersonate:{impersonation_code}"
    cache.set(
        cache_key,
        {
            "access": response_data["access"],
            "refresh": response_data["refresh"],
            "user": user_payload,
        },
        timeout=120,
    )
    response_data["impersonation_code"] = impersonation_code

    return Response(response_data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([AllowAny])
def impersonate_exchange_status(request):
    """Diagnostic: GET /api/auth/impersonate-exchange/status/ returns 200 if this app revision is deployed."""
    return Response({"status": "ok", "endpoint": "impersonate-exchange"}, status=status.HTTP_200_OK)


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
        return Response(
            {"error": "Missing code parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    cache_key = f"impersonate:{code}"
    data = cache.get(cache_key)
    if not data:
        logger.warning("impersonate_exchange: code not found or expired (key=%s)", cache_key[:20] + "...")
        return Response(
            {"error": "Invalid or expired code."},
            status=status.HTTP_404_NOT_FOUND,
        )
    cache.delete(cache_key)
    return Response(data, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def verify_email(request):
    """
    Verify a user's email using a code or token.
    """
    import urllib.parse

    serializer = EmailVerificationSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user = serializer.validated_data["user"]
    code = serializer.validated_data.get("code")
    token = serializer.validated_data.get("token")

    # Check if email is already verified
    if user.email_verified:
        return Response(
            {"message": "Email is already verified."},
            status=status.HTTP_200_OK,
        )

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
        return Response(
            {"error": "Either code or token must be provided."},
            status=status.HTTP_400_BAD_REQUEST,
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
            return Response(
                {"message": "Email is already verified."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"error": "Invalid or expired verification code."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if verification.is_expired:
        verification.delete()
        return Response(
            {"error": "Verification code has expired. Please request a new one."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Mark as verified
    verification.mark_verified()
    user.email_verified = True
    user.save(update_fields=["email_verified"])

    return Response(
        {"message": "Email verified successfully."},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def resend_verification(request):
    """
    Resend email verification code to the user's email.
    """
    email = request.data.get("email")
    if not email:
        return Response(
            {"error": "Email is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {"error": "User with this email does not exist."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Check if email is already verified
    if user.email_verified:
        return Response(
            {"message": "Email is already verified."},
            status=status.HTTP_200_OK,
        )

    # Check if user is requesting for their own email or is admin
    if request.user.email != email and not request.user.is_staff:
        return Response(
            {"error": "You can only request verification for your own email."},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        expiry_hours = getattr(settings, "EMAIL_VERIFICATION_EXPIRY_HOURS", 48)
        verification = EmailVerification.create_for_user(
            user, expiry_hours=expiry_hours
        )

        # Get language from request header or default to 'en'
        language = request.META.get("HTTP_ACCEPT_LANGUAGE", "en")
        if "ar" in language.lower():
            language = "ar"
        else:
            language = "en"

        sent = send_email_verification(user, verification, language=language)

        return Response(
            {
                "message": (
                    "Verification code sent successfully."
                    if sent
                    else "Failed to send verification email."
                ),
                "sent": sent,
                "expires_at": verification.expires_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )
    except Exception as exc:
        logger.error("Failed to resend verification email: %s", exc)
        return Response(
            {"error": "Failed to send verification email. Please try again later."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def check_registration_availability(request):
    """
    Check if company domain, email, username, or phone are available prior to registration.
    """
    serializer = RegistrationAvailabilitySerializer(data=request.data)
    if serializer.is_valid():
        return Response({"available": True}, status=status.HTTP_200_OK)

    return Response(
        {"available": False, "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
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
        return Response(
            {"message": "If the email exists, a password reset link has been sent."},
            status=status.HTTP_200_OK,
        )

    user = serializer.validated_data.get("user")
    if not user:
        # Don't reveal if email exists or not
        return Response(
            {"message": "If the email exists, a password reset link has been sent."},
            status=status.HTTP_200_OK,
        )

    # Create password reset token
    try:
        expiry_hours = getattr(settings, "PASSWORD_RESET_EXPIRY_HOURS", 1)
        reset = PasswordReset.create_for_user(user, expiry_hours=expiry_hours)
        # Get language from request header or default to 'en'
        language = request.META.get("HTTP_ACCEPT_LANGUAGE", "en")
        if "ar" in language.lower():
            language = "ar"
        else:
            language = "en"
        sent = send_password_reset_email(user, reset, language=language)

        return Response(
            {
                "message": "If the email exists, a password reset link has been sent.",
                "sent": sent,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as exc:
        logger.warning("Unable to send password reset email: %s", exc)
        # Don't reveal error to user
        return Response(
            {
                "message": "If the email exists, a password reset link has been sent.",
                "sent": False,
            },
            status=status.HTTP_200_OK,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
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
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
        return Response(
            {"error": "Either code or token must be provided."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Find the reset record
    reset = PasswordReset.objects.filter(**filters).order_by("-created_at").first()

    if not reset:
        return Response(
            {"error": "Invalid or expired reset code."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if reset.is_expired:
        reset.delete()
        return Response(
            {"error": "Reset code has expired. Please request a new one."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if reset.is_used:
        return Response(
            {"error": "This reset code has already been used."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Reset password
    user.set_password(new_password)
    user.save()

    # Mark reset as used
    reset.mark_used()

    return Response(
        {"message": "Password has been reset successfully."},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
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

        # Return normalized error format
        if error_message:
            return Response(
                {"error": error_message}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user = serializer.validated_data.get("user")
    if not user:
        return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

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
                is_employee_user = user.role == "employee"

                if is_employee_user:
                    # Employees see "account temporarily inactive" message
                    error_data = {
                        "error": "Your account is temporarily inactive",
                        "code": "ACCOUNT_TEMPORARILY_INACTIVE",
                    }
                else:
                    # Admins see subscription inactive message
                    error_data = {
                        "error": "Your subscription is not active. Please contact support or complete your payment to access the system.",
                        "code": "SUBSCRIPTION_INACTIVE",
                    }
                    if subscription:
                        error_data["subscriptionId"] = subscription.id

                return Response(error_data, status=status.HTTP_403_FORBIDDEN)

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

        # Get language from request header or default to 'ar'
        language = request.META.get("HTTP_ACCEPT_LANGUAGE", "ar")
        if "en" in language.lower():
            language = "en"
        else:
            language = "ar"
        if not is_apple_demo and not is_google_demo:
            sent = send_two_factor_auth_email(user, two_fa, language=language)
        else:
            sent = True
        return Response(
            {
                "message": "2FA code has been sent to your email.",
                "sent": sent,
                "token": two_fa.token,  # Return token for verification
            },
            status=status.HTTP_200_OK,
        )
    except Exception as exc:
        logger.warning("Unable to send 2FA email: %s", exc)
        return Response(
            {
                "error": "Failed to send 2FA code. Please try again.",
                "sent": False,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        return Response(
            {"error": "Username and password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Find user
    user = None
    if "@" in username_or_email:
        try:
            user = User.objects.get(email__iexact=username_or_email)
        except User.DoesNotExist:
            return Response(
                {"error": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
            )
    else:
        try:
            user = User.objects.get(username__iexact=username_or_email)
        except User.DoesNotExist:
            return Response(
                {"error": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
            )

    # Verify password
    if not user.check_password(password):
        return Response(
            {"error": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
        )

    # Now verify 2FA code
    serializer = VerifyTwoFactorAuthSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
        return Response(
            {"error": "Either code or token must be provided."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Find the 2FA record
    two_fa = TwoFactorAuth.objects.filter(**filters).order_by("-created_at").first()

    if not two_fa:
        return Response(
            {"error": "Invalid or expired 2FA code."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if two_fa.is_expired:
        two_fa.delete()
        return Response(
            {"error": "2FA code has expired. Please request a new one."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if two_fa.is_verified:
        return Response(
            {"error": "This 2FA code has already been used."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Mark as verified
    two_fa.mark_verified()

    # Check subscription for all users except Super Admin
    # Super Admin doesn't need active subscription
    if not user.is_super_admin():
        from subscriptions.models import Subscription

        # Check if user has a company
        if not user.company:
            # User without company cannot login (except super admin)
            error_data = {
                "error": "Your account is not associated with a company. Please contact support.",
                "code": "NO_COMPANY",
            }
            return Response(error_data, status=status.HTTP_403_FORBIDDEN)

        # Check if company has an active subscription
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
            is_employee_user = user.role == "employee"

            if is_employee_user:
                # Employees see "account temporarily inactive" message
                error_data = {
                    "error": "Your account is temporarily inactive",
                    "code": "ACCOUNT_TEMPORARILY_INACTIVE",
                }
            else:
                # Admins see subscription inactive message
                error_data = {
                    "error": "Your subscription is not active. Please contact support or complete your payment to access the system.",
                    "code": "SUBSCRIPTION_INACTIVE",
                }
                if subscription:
                    error_data["subscriptionId"] = subscription.id

            return Response(error_data, status=status.HTTP_403_FORBIDDEN)

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
        },
    }

    return Response(response_data, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def update_fcm_token(request):
    """
    Update FCM token and language for the authenticated user
    """
    fcm_token = request.data.get("fcm_token", "").strip()
    language = request.data.get("language", "").strip()

    if not fcm_token:
        return Response(
            {"error": "fcm_token is required"}, status=status.HTTP_400_BAD_REQUEST
        )

    user = request.user
    user.fcm_token = fcm_token

    # Update language if provided
    if language in ["ar", "en"]:
        user.language = language

    user.save(update_fields=["fcm_token", "language"])

    logger.info(f"FCM token and language updated for user {user.username}")

    return Response(
        {"message": "FCM token updated successfully"}, status=status.HTTP_200_OK
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def update_language(request):
    """
    Update language preference for the authenticated user
    """
    language = request.data.get("language", "").strip()

    if not language:
        return Response(
            {"error": "language is required"}, status=status.HTTP_400_BAD_REQUEST
        )

    if language not in ["ar", "en"]:
        return Response(
            {"error": 'language must be either "ar" or "en"'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = request.user
    user.language = language
    user.save(update_fields=["language"])

    logger.info(f"Language updated to {language} for user {user.username}")

    return Response(
        {"message": "Language updated successfully", "language": language},
        status=status.HTTP_200_OK,
    )


class LimitedAdminViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing LimitedAdmin instances.
    Superusers or limited admins with can_manage_limited_admins can manage.
    List returns all limited admins (active and inactive) so they remain visible in the table.
    """
    serializer_class = LimitedAdminSerializer
    permission_classes = [IsAuthenticated, CanManageLimitedAdmins]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__username', 'user__email', 'user__first_name', 'user__last_name']
    ordering_fields = ['created_at', 'updated_at', 'user__username']
    ordering = ['-created_at']

    def get_queryset(self):
        # Return all limited admins (active and inactive) so deactivated ones stay visible in the table
        return LimitedAdmin.objects.all().select_related('user', 'created_by')
    
    def get_serializer_class(self):
        """Use CreateLimitedAdminSerializer for creation"""
        if self.action == 'create':
            return CreateLimitedAdminSerializer
        return LimitedAdminSerializer
    
    def create(self, request, *args, **kwargs):
        """Create limited admin and return response using LimitedAdminSerializer."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        # Instance is LimitedAdmin; serialize with LimitedAdminSerializer for response
        output_serializer = LimitedAdminSerializer(serializer.instance)
        headers = self.get_success_headers(output_serializer.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    def perform_create(self, serializer):
        """Set created_by when creating"""
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        """Toggle is_active status of limited admin"""
        # Permission is already checked by CanManageLimitedAdmins permission class
        limited_admin = self.get_object()
        limited_admin.is_active = not limited_admin.is_active
        limited_admin.save(update_fields=['is_active'])
        
        serializer = self.get_serializer(limited_admin)
        return Response(serializer.data)


class SupervisorViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing supervisors (company-scoped).
    Only company admin can list/create/update/delete/toggle supervisors in their company.
    """
    serializer_class = SupervisorSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanManageSupervisors]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__username', 'user__email', 'user__first_name', 'user__last_name']
    ordering_fields = ['created_at', 'updated_at', 'user__username']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        if not user.company:
            return SupervisorPermission.objects.none()
        return SupervisorPermission.objects.filter(user__company=user.company).select_related('user')

    def get_serializer_class(self):
        if self.action == 'create':
            return CreateSupervisorSerializer
        return SupervisorSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company = request.user.company
        if not company:
            return Response(
                {"error": "You must belong to a company to create supervisors."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer.save(company=company)
        output_serializer = SupervisorSerializer(serializer.instance)
        headers = self.get_success_headers(output_serializer.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        sp = self.get_object()
        sp.is_active = not sp.is_active
        sp.save(update_fields=['is_active'])
        serializer = self.get_serializer(sp)
        return Response(serializer.data)
