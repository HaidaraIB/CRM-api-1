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

class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Custom view to get a JWT token with user information
    """

    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]


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

    @staticmethod
    def _parse_roles_param(raw_value):
        if not raw_value:
            return []
        role_aliases = {
            "owner": Role.ADMIN.value,
        }
        roles = []
        for item in raw_value.split(","):
            role = item.strip().lower()
            if not role:
                continue
            roles.append(role_aliases.get(role, role))
        return list(dict.fromkeys(roles))

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        # Supervisor with can_manage_users: full company users; can_manage_leads: list only (for Activities filter)
        if user.is_supervisor() and user.company:
            if user.supervisor_has_permission("manage_users") or user.supervisor_has_permission("manage_leads"):
                queryset = queryset.filter(company=user.company)
            else:
                queryset = queryset.filter(id=user.id)
        elif user.company and user.is_admin():
            queryset = queryset.filter(company=user.company)
        else:
            # Employee can only access their own profile
            queryset = queryset.filter(id=user.id)

        include_roles = self._parse_roles_param(self.request.query_params.get("roles"))
        if include_roles:
            queryset = queryset.filter(role__in=include_roles)

        exclude_roles = self._parse_roles_param(self.request.query_params.get("exclude_roles"))
        if exclude_roles:
            queryset = queryset.exclude(role__in=exclude_roles)

        if self.action == "list":
            queryset = queryset.select_related("company")

        return queryset

    def perform_create(self, serializer):
        """Create user and automatically set company from request user, then link company owner if user is admin"""
        # Get company from request user (the user creating this new user)
        request_user = self.request.user
        company = (
            request_user.company if request_user and request_user.company else None
        )

        if company and not request_user.is_super_admin():
            from subscriptions.entitlements import require_quota
            owner_id = getattr(company, "owner_id", None)
            current_users = User.objects.filter(company=company).exclude(id=owner_id).count()
            require_quota(
                company,
                "max_employees",
                current_count=current_users,
                requested_delta=1,
                message="You have reached your plan employee limit. Please upgrade your plan to add more users.",
                error_key="plan_quota_max_employees_exceeded",
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
        return success_response(data=serializer.data)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def presence_heartbeat(self, request):
        source = str(request.data.get("source", "unknown")).strip().lower()
        allowed_sources = {"web", "mobile", "unknown"}
        if source not in allowed_sources:
            source = "unknown"

        user = request.user
        user.last_seen_at = timezone.now()
        user.last_seen_source = source
        user.save(update_fields=["last_seen_at", "last_seen_source"])

        return success_response(
            message="Presence heartbeat recorded.",
            data={
                "last_seen_at": user.last_seen_at,
                "last_seen_source": user.last_seen_source,
            },
        )

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
                return error_response(
                    "Current password is incorrect.",
                    code="invalid_password",
                )

            # Validate new password
            try:
                validate_password(new_password, user)
            except ValidationError as e:
                return error_response(
                    " ".join(e.messages),
                    code="password_validation_failed",
                )

            # Set new password
            user.set_password(new_password)
            user.save()

            return success_response(message="Password changed successfully.")

        return validation_error_response(serializer.errors)


