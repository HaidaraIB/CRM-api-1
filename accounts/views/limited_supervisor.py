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
from django.db import transaction
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
        return success_response(
            data=output_serializer.data,
            status_code=status.HTTP_201_CREATED,
            headers=headers,
        )
    
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
        return success_response(data=serializer.data)


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
            return error_response(
                "You must belong to a company to create supervisors.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        serializer.save(company=company)
        output_serializer = SupervisorSerializer(serializer.instance)
        headers = self.get_success_headers(output_serializer.data)
        return success_response(
            data=output_serializer.data,
            status_code=status.HTTP_201_CREATED,
            headers=headers,
        )

    def destroy(self, request, *args, **kwargs):
        """
        Remove the supervisor's User account, not only SupervisorPermission.
        Default ModelViewSet.destroy would delete only SupervisorPermission; the User
        row would remain and still appear on the employees list.
        """
        sp = self.get_object()
        subject = sp.user
        if subject.id == request.user.id:
            return error_response(
                "You cannot delete your own account.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if subject.role != Role.SUPERVISOR.value:
            return error_response(
                "This record is not linked to a supervisor user.",
                code="invalid_target",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        company = request.user.company
        if company and getattr(company, "owner_id", None) == subject.id:
            return error_response(
                "Cannot delete the company owner.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        with transaction.atomic():
            subject.delete()
        return success_response(status_code=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        sp = self.get_object()
        sp.is_active = not sp.is_active
        sp.save(update_fields=['is_active'])
        serializer = self.get_serializer(sp)
        return success_response(data=serializer.data)
