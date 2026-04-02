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


