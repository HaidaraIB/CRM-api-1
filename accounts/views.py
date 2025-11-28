from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import User, Role, EmailVerification, PasswordReset, TwoFactorAuth
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
)
from .permissions import CanAccessUser
from companies.models import Company
from django.conf import settings
from .utils import send_email_verification, send_password_reset_email, send_two_factor_auth_email
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
    permission_classes = [IsAuthenticated, CanAccessUser]
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
        
        # Employee can only access their own profile
        return queryset.filter(id=user.id)

    def perform_create(self, serializer):
        """Create user and automatically link company owner if user is admin"""
        user = serializer.save()
        
        # إذا كان المستخدم admin وله company، ربط Company.owner به تلقائياً
        if user.role == Role.ADMIN.value and user.company:
            # إذا لم يكن للـ company owner أو كان owner مختلف، ربط المستخدم كـ owner
            if not user.company.owner:
                user.company.owner = user
                user.company.save(update_fields=['owner'])
            elif user.company.owner != user:
                # إذا كان هناك owner آخر، يمكن اختيار استبداله أو عدم التحديث
                # هنا سنستبدله إذا كان المستخدم الجديد admin
                user.company.owner = user
                user.company.save(update_fields=['owner'])
    
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
                if old_company and old_company != new_company and old_company.owner == user:
                    old_company.owner = None
                    old_company.save(update_fields=['owner'])
                
                # ربط Company.owner بالمستخدم الجديد
                if not new_company.owner or new_company.owner != user:
                    new_company.owner = user
                    new_company.save(update_fields=['owner'])
        elif old_company and old_company.owner == user:
            # إذا لم يعد المستخدم admin أو تمت إزالة company، إزالة owner من company
            old_company.owner = None
            old_company.save(update_fields=['owner'])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def get_serializer_class(self):
        if self.action == "list":
            return UserListSerializer
        return UserSerializer

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def me(self, request):
        serializer = UserSerializer(request.user, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def change_password(self, request):
        """
        Change password for the current authenticated user
        """
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            user = request.user
            current_password = serializer.validated_data['current_password']
            new_password = serializer.validated_data['new_password']
            
            # Verify current password
            if not user.check_password(current_password):
                return Response(
                    {'error': 'Current password is incorrect.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate new password
            try:
                validate_password(new_password, user)
            except ValidationError as e:
                return Response(
                    {'error': ' '.join(e.messages)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Set new password
            user.set_password(new_password)
            user.save()
            
            return Response(
                {'message': 'Password changed successfully.'},
                status=status.HTTP_200_OK
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
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
        company = result['company']
        owner = result['owner']
        subscription = result.get('subscription')
        
        # Generate JWT tokens
        refresh = RefreshToken.for_user(owner)
        
        # Prepare response data
        response_data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': owner.id,
                'username': owner.username,
                'email': owner.email,
                'first_name': owner.first_name,
                'last_name': owner.last_name,
                'phone': owner.phone or "",
                'role': owner.role,
                'email_verified': owner.email_verified,
                'company': company.id,
                'company_name': company.name,
                'company_specialization': company.specialization,
            },
            'company': {
                'id': company.id,
                'name': company.name,
                'domain': company.domain,
                'specialization': company.specialization,
            },
        }
        
        if subscription:
            response_data['subscription'] = {
                'id': subscription.id,
                'plan_id': subscription.plan.id,
                'plan_name': subscription.plan.name,
                'is_active': subscription.is_active,
                'end_date': subscription.end_date.isoformat(),
            }

        verification_info = {}
        try:
            expiry_hours = getattr(settings, "EMAIL_VERIFICATION_EXPIRY_HOURS", 48)
            verification = EmailVerification.create_for_user(owner, expiry_hours=expiry_hours)
            # Get language from request header or default to 'en'
            language = request.META.get('HTTP_ACCEPT_LANGUAGE', 'en')
            if 'ar' in language.lower():
                language = 'ar'
            else:
                language = 'en'
            sent = send_email_verification(owner, verification, language=language)
            verification_info = {
                "sent": sent,
                "expires_at": verification.expires_at.isoformat(),
            }
        except Exception as exc:
            logger.warning("Unable to send verification email: %s", exc)
            verification_info = {
                "sent": False,
                "error": str(exc),
            }

        response_data["email_verification"] = verification_info
        
        return Response(response_data, status=status.HTTP_201_CREATED)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
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
        EmailVerification.objects.filter(**filters)
        .order_by("-created_at")
        .first()
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


@api_view(['POST'])
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


@api_view(['POST'])
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
            status=status.HTTP_200_OK
        )
    
    user = serializer.validated_data.get("user")
    if not user:
        # Don't reveal if email exists or not
        return Response(
            {"message": "If the email exists, a password reset link has been sent."},
            status=status.HTTP_200_OK
        )
    
    # Create password reset token
    try:
        expiry_hours = getattr(settings, "PASSWORD_RESET_EXPIRY_HOURS", 1)
        reset = PasswordReset.create_for_user(user, expiry_hours=expiry_hours)
        # Get language from request header or default to 'en'
        language = request.META.get('HTTP_ACCEPT_LANGUAGE', 'en')
        if 'ar' in language.lower():
            language = 'ar'
        else:
            language = 'en'
        sent = send_password_reset_email(user, reset, language=language)
        
        return Response(
            {
                "message": "If the email exists, a password reset link has been sent.",
                "sent": sent,
            },
            status=status.HTTP_200_OK
        )
    except Exception as exc:
        logger.warning("Unable to send password reset email: %s", exc)
        # Don't reveal error to user
        return Response(
            {
                "message": "If the email exists, a password reset link has been sent.",
                "sent": False,
            },
            status=status.HTTP_200_OK
        )


@api_view(['POST'])
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
    reset = (
        PasswordReset.objects.filter(**filters)
        .order_by("-created_at")
        .first()
    )
    
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


@api_view(['POST'])
@permission_classes([AllowAny])
def request_two_factor_auth(request):
    """
    Request 2FA code - sends email with 2FA code
    POST /api/auth/request-2fa/
    Body: { username: string }
    Response: { message: string, sent: bool, token: string }
    """
    serializer = RequestTwoFactorAuthSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    user = serializer.validated_data.get("user")
    if not user:
        return Response(
            {"error": "User not found."},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Create 2FA code
    try:
        expiry_minutes = 10  # 2FA codes expire in 10 minutes
        two_fa = TwoFactorAuth.create_for_user(user, expiry_minutes=expiry_minutes)
        # Get language from request header or default to 'ar'
        language = request.META.get('HTTP_ACCEPT_LANGUAGE', 'ar')
        if 'en' in language.lower():
            language = 'en'
        else:
            language = 'ar'
        sent = send_two_factor_auth_email(user, two_fa, language=language)
        
        return Response(
            {
                "message": "2FA code has been sent to your email.",
                "sent": sent,
                "token": two_fa.token,  # Return token for verification
            },
            status=status.HTTP_200_OK
        )
    except Exception as exc:
        logger.warning("Unable to send 2FA email: %s", exc)
        return Response(
            {
                "error": "Failed to send 2FA code. Please try again.",
                "sent": False,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
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
    username_or_email = request.data.get('username', '').strip()
    password = request.data.get('password', '')
    
    if not username_or_email or not password:
        return Response(
            {"error": "Username and password are required."},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Find user
    user = None
    if '@' in username_or_email:
        try:
            user = User.objects.get(email__iexact=username_or_email)
        except User.DoesNotExist:
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED
            )
    else:
        try:
            user = User.objects.get(username__iexact=username_or_email)
        except User.DoesNotExist:
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED
            )
    
    # Verify password
    if not user.check_password(password):
        return Response(
            {"error": "Invalid credentials."},
            status=status.HTTP_401_UNAUTHORIZED
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
    two_fa = (
        TwoFactorAuth.objects.filter(**filters)
        .order_by("-created_at")
        .first()
    )
    
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
    
    # Generate JWT tokens
    refresh = RefreshToken.for_user(user)
    
    # Prepare response data
    response_data = {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'phone': user.phone or "",
            'role': user.role,
            'email_verified': user.email_verified,
            'company': user.company.id if user.company else None,
            'company_name': user.company.name if user.company else None,
            'company_specialization': user.company.specialization if user.company else None,
        },
    }
    
    return Response(response_data, status=status.HTTP_200_OK)
