from rest_framework import serializers
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from drf_spectacular.utils import extend_schema_field
from .models import User, Role, TwoFactorAuth, LimitedAdmin, SupervisorPermission
from companies.models import Company
from subscriptions.models import Plan, Subscription, SubscriptionStatus, BillingCycle
from .two_factor_policy import is_company_owner, is_trusted_device_valid
from .utils import get_email_language_for_user, send_two_factor_auth_email
from django.conf import settings

# Inventory permission keys; only one is allowed per company based on specialization
INVENTORY_PERM_KEYS = ('can_manage_products', 'can_manage_services', 'can_manage_real_estate')


def filter_inventory_permissions_for_company(perms_dict, company):
    """
    Ensure only the inventory permission matching the company's specialization is allowed.
    real_estate -> can_manage_real_estate only; products -> can_manage_products only; services -> can_manage_services only.
    """
    if not company or not getattr(company, 'specialization', None):
        for k in INVENTORY_PERM_KEYS:
            perms_dict[k] = False
        return perms_dict
    spec = company.specialization
    if spec == 'real_estate':
        perms_dict['can_manage_products'] = False
        perms_dict['can_manage_services'] = False
    elif spec == 'products':
        perms_dict['can_manage_real_estate'] = False
        perms_dict['can_manage_services'] = False
    elif spec == 'services':
        perms_dict['can_manage_real_estate'] = False
        perms_dict['can_manage_products'] = False
    else:
        for k in INVENTORY_PERM_KEYS:
            perms_dict[k] = False
    return perms_dict
from datetime import timedelta
from django.utils import timezone


class UserSerializer(serializers.ModelSerializer):
    is_me = serializers.SerializerMethodField()
    company_name = serializers.CharField(source="company.name", read_only=True)
    company_specialization = serializers.CharField(source="company.specialization", read_only=True)
    company = serializers.SerializerMethodField()  # Override to return full company object for reads
    company_id = serializers.PrimaryKeyRelatedField(
        queryset=Company.objects.all(),
        source='company',
        write_only=True,
        required=False,
        allow_null=True
    )  # Allow writing company via company_id field
    password = serializers.CharField(write_only=True, required=False)
    limited_admin = serializers.SerializerMethodField()
    supervisor_permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "phone",
            "profile_photo",
            "role",
            "company",
            "company_id",
            "company_name",
            "company_specialization",
            "is_active",
            "is_staff",
            "is_superuser",
            "email_verified",
            "phone_verified",
            "date_joined",
            "last_login",
            "is_me",
            "fcm_token",  # Include FCM token (read-only for security)
            "language",  # User preferred language
            "limited_admin",
            "supervisor_permissions",
        ]
        read_only_fields = ["id", "date_joined", "last_login", "email_verified", "phone_verified", "fcm_token"]
        extra_kwargs = {
            "email": {"required": True},
        }

    def create(self, validated_data):
        """Create user with password"""
        password = validated_data.pop('password', None)
        if not password:
            raise serializers.ValidationError({'password': 'Password is required when creating a user.'})
        user = User.objects.create_user(
            password=password,
            **validated_data
        )
        return user

    def update(self, instance, validated_data):
        """Update user and handle password change"""
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance

    def validate_role(self, value):
        valid_roles = {r.value for r in Role}
        if value not in valid_roles:
            raise serializers.ValidationError("Invalid role.")
        if value == Role.SUPER_ADMIN.value:
            instance = getattr(self, "instance", None)
            target_is_superuser = bool(getattr(instance, "is_superuser", False))
            incoming_is_superuser = self.initial_data.get("is_superuser")
            if incoming_is_superuser is not None:
                if isinstance(incoming_is_superuser, bool):
                    target_is_superuser = incoming_is_superuser
                else:
                    target_is_superuser = str(incoming_is_superuser).strip().lower() in {"1", "true", "yes", "on"}
            if not target_is_superuser:
                raise serializers.ValidationError("SUPER_ADMIN role is only allowed for superusers.")
        return value

    @extend_schema_field(serializers.BooleanField())
    def get_is_me(self, obj):
        """Check if this user is the current user"""
        request = self.context.get("request")
        if request and request.user:
            return obj.id == request.user.id
        return False
    
    def get_company(self, obj):
        """Return full company object with subscription information"""
        if not obj.company:
            return None
        
        company = obj.company
        # Get the active subscription first, if not found get the latest subscription (active or inactive)
        subscription = Subscription.objects.filter(
            company=company,
            is_active=True
        ).order_by('-created_at').first()
        
        # If no active subscription, get the latest subscription (even if inactive) for payment link
        if not subscription:
            subscription = Subscription.objects.filter(
                company=company
            ).order_by('-created_at').first()
        
        company_data = {
            "id": company.id,
            "name": company.name,
            "domain": company.domain,
            "specialization": company.specialization,
            "auto_assign_enabled": company.auto_assign_enabled,
            "re_assign_enabled": company.re_assign_enabled,
            "re_assign_hours": company.re_assign_hours,
            "free_trial_consumed": getattr(company, "free_trial_consumed", False),
        }
        
        if subscription:
            now = timezone.now()
            days_remaining = None
            if subscription.end_date:
                days_remaining = max(0, (subscription.end_date - now).days)
            pending = subscription.pending_plan
            company_data["subscription"] = {
                "id": subscription.id,
                "plan": {
                    "id": subscription.plan.id,
                    "name": subscription.plan.name,
                    "name_ar": subscription.plan.name_ar if subscription.plan.name_ar else None,
                    "tier": getattr(subscription.plan, "tier", 0),
                },
                "is_active": subscription.is_active,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "current_period_start": subscription.current_period_start.isoformat()
                if getattr(subscription, "current_period_start", None)
                else None,
                "billing_cycle": getattr(subscription, "billing_cycle", None),
                "subscription_status": getattr(subscription, "subscription_status", None),
                "days_remaining_in_period": days_remaining,
                "pending_plan": (
                    {"id": pending.id, "name": pending.name, "tier": getattr(pending, "tier", 0)}
                    if pending
                    else None
                ),
                "pending_billing_cycle": getattr(subscription, "pending_billing_cycle", None),
                "auto_renew": subscription.auto_renew,
            }
            # Attach computed entitlements snapshot for frontend gating/UX.
            try:
                from subscriptions.entitlements import (
                    build_company_entitlements,
                    get_monthly_usage_snapshot,
                )

                ent = build_company_entitlements(company)
                company_data["subscription"]["entitlements"] = {
                    "plan_id": ent.plan_id,
                    "plan_name": ent.plan_name,
                    "quotas": {
                        "max_employees": ent.max_employees,
                        "max_users": ent.max_users,
                        "max_clients": ent.max_clients,
                        **(ent.extra_limits or {}),
                    },
                    "features": ent.features,
                    "usage_limits_monthly": ent.usage_limits_monthly,
                    "usage_month_to_date": get_monthly_usage_snapshot(company),
                }
            except Exception:
                company_data["subscription"]["entitlements"] = None
        else:
            company_data["subscription"] = None
        
        return company_data
    
    def get_limited_admin(self, obj):
        """Return limited admin permissions if user is a limited admin"""
        try:
            limited_admin = LimitedAdmin.objects.select_related('user').get(user=obj)
            return {
                "id": limited_admin.id,
                "is_active": limited_admin.is_active,
                "permissions": {
                    "can_view_dashboard": limited_admin.can_view_dashboard,
                    "can_manage_tenants": limited_admin.can_manage_tenants,
                    "can_manage_subscriptions": limited_admin.can_manage_subscriptions,
                    "can_manage_payment_gateways": limited_admin.can_manage_payment_gateways,
                    "can_view_reports": limited_admin.can_view_reports,
                    "can_manage_communication": limited_admin.can_manage_communication,
                    "can_manage_settings": limited_admin.can_manage_settings,
                    "can_manage_limited_admins": limited_admin.can_manage_limited_admins,
                }
            }
        except LimitedAdmin.DoesNotExist:
            return None

    def get_supervisor_permissions(self, obj):
        """Return supervisor permissions if user is a supervisor"""
        if obj.role != Role.SUPERVISOR.value:
            return None
        try:
            sp = SupervisorPermission.objects.get(user=obj)
            return {
                "id": sp.id,
                "is_active": sp.is_active,
                "permissions": {
                    "can_manage_leads": sp.can_manage_leads,
                    "can_manage_deals": sp.can_manage_deals,
                    "can_manage_tasks": sp.can_manage_tasks,
                    "can_view_reports": sp.can_view_reports,
                    "can_manage_users": sp.can_manage_users,
                    "can_manage_products": sp.can_manage_products,
                    "can_manage_services": sp.can_manage_services,
                    "can_manage_real_estate": sp.can_manage_real_estate,
                    "can_manage_settings": sp.can_manage_settings,
                },
            }
        except SupervisorPermission.DoesNotExist:
            return None


class UserListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""

    company_name = serializers.CharField(source="company.name", read_only=True)
    company_specialization = serializers.CharField(source="company.specialization", read_only=True)
    is_me = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "profile_photo",
            "role",
            "company",
            "company_name",
            "company_specialization",
            "is_active",
            "email_verified",
            "date_joined",
            "is_me",
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_is_me(self, obj):
        """Check if this user is the current user"""
        request = self.context.get("request")
        if request and request.user:
            return obj.id == request.user.id
        return False


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Custom serializer to return user information with token"""
    
    username_field = 'username'  # Default field name
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Allow username field to accept both username and email
        self.fields['username'] = serializers.CharField(required=True)

    def validate(self, attrs):
        username_or_email = attrs.get('username', '').strip()
        password = attrs.get('password', '')
        
        # Try to find user by email or username
        user = None
        if '@' in username_or_email:
            # Looks like an email
            try:
                user = User.objects.get(email__iexact=username_or_email)
                # Replace username with actual username for authentication
                attrs['username'] = user.username
            except User.DoesNotExist:
                pass
        else:
            # Try username
            try:
                user = User.objects.get(username__iexact=username_or_email)
            except User.DoesNotExist:
                pass
        
        # If user not found, let parent class handle the error
        if not user:
            # Use original username for parent validation (will raise error if invalid)
            attrs['username'] = username_or_email
        
        # Validate using parent class
        data = super().validate(attrs)
        request = self.context.get("request")

        # Keep login subscription-safe to match 2FA endpoint behavior.
        if not self.user.is_super_admin():
            if not self.user.company:
                raise serializers.ValidationError(
                    {"error": "Your account is not associated with a company. Please contact support."}
                )

            has_active_subscription = Subscription.objects.filter(
                company=self.user.company, is_active=True
            ).exists()
            if not has_active_subscription:
                subscription = (
                    Subscription.objects.filter(company=self.user.company)
                    .order_by("-created_at")
                    .first()
                )
                is_employee_user = self.user.role in ("employee", "data_entry")
                if is_employee_user:
                    raise serializers.ValidationError(
                        {"error": "Your account is temporarily inactive", "code": "ACCOUNT_TEMPORARILY_INACTIVE"}
                    )
                raise serializers.ValidationError(
                    {
                        "error": "Your subscription is not active. Please contact support or Complete Your Payment to access the system.",
                        "code": "SUBSCRIPTION_INACTIVE",
                        "subscriptionId": subscription.id if subscription else None,
                    }
                )

        # Owner-only intelligent 2FA: challenge only when trusted-device check fails.
        if request and is_company_owner(self.user) and not is_trusted_device_valid(self.user, request):
            expiry_minutes = 10

            demo_2fa_code = None
            is_google_demo = (
                settings.DEMO_GOOGLE_ACCOUNT_USERNAME
                and self.user.username.lower() == settings.DEMO_GOOGLE_ACCOUNT_USERNAME.lower()
            ) or (
                settings.DEMO_GOOGLE_ACCOUNT_EMAIL
                and self.user.email.lower() == settings.DEMO_GOOGLE_ACCOUNT_EMAIL.lower()
            )
            is_apple_demo = (
                settings.DEMO_APPLE_ACCOUNT_USERNAME
                and self.user.username.lower() == settings.DEMO_APPLE_ACCOUNT_USERNAME.lower()
            ) or (
                settings.DEMO_APPLE_ACCOUNT_EMAIL
                and self.user.email.lower() == settings.DEMO_APPLE_ACCOUNT_EMAIL.lower()
            )
            if is_google_demo and getattr(settings, "DEMO_GOOGLE_ACCOUNT_2FA_CODE", ""):
                demo_2fa_code = settings.DEMO_GOOGLE_ACCOUNT_2FA_CODE
            elif is_apple_demo and getattr(settings, "DEMO_APPLE_ACCOUNT_2FA_CODE", ""):
                demo_2fa_code = settings.DEMO_APPLE_ACCOUNT_2FA_CODE

            if demo_2fa_code:
                TwoFactorAuth.objects.filter(user=self.user, is_verified=False).delete()
                import uuid

                two_fa = TwoFactorAuth.objects.create(
                    user=self.user,
                    code=demo_2fa_code,
                    token=uuid.uuid4().hex,
                    expires_at=timezone.now() + timedelta(minutes=expiry_minutes),
                )
                sent = True
            else:
                two_fa = TwoFactorAuth.create_for_user(self.user, expiry_minutes=expiry_minutes)
                language = get_email_language_for_user(self.user, request, default="ar")
                sent = send_two_factor_auth_email(self.user, two_fa, language=language)

            return {
                "requires_two_factor": True,
                "sent": sent,
                "token": two_fa.token,
                "message": "2FA code has been sent to your email.",
                "user": build_user_auth_payload(self.user, request),
            }

        # Get limited admin profile if exists
        limited_admin = None
        try:
            limited_admin = LimitedAdmin.objects.select_related('user').get(user=self.user)
        except LimitedAdmin.DoesNotExist:
            pass
        
        # Add user information to the response
        user_data = {
            "id": self.user.id,
            "username": self.user.username,
            "email": self.user.email,
            "first_name": self.user.first_name,
            "last_name": self.user.last_name,
            "phone": self.user.phone or "",
            "profile_photo": self.context['request'].build_absolute_uri(self.user.profile_photo.url) if self.user.profile_photo and self.context.get('request') else (self.user.profile_photo.url if self.user.profile_photo else None),
            "role": self.user.role,
            "company": self.user.company.id if self.user.company else None,
            "company_name": self.user.company.name if self.user.company else None,
            "company_specialization": self.user.company.specialization if self.user.company else None,
            "is_active": self.user.is_active,
            "email_verified": self.user.email_verified,
            "phone_verified": getattr(self.user, "phone_verified", False),
            "is_superuser": self.user.is_superuser,
            "language": getattr(self.user, "language", None) or "ar",
        }
        
        # Add limited admin permissions if user is a limited admin
        if limited_admin:
            user_data["limited_admin"] = {
                "id": limited_admin.id,
                "is_active": limited_admin.is_active,
                "permissions": {
                    "can_view_dashboard": limited_admin.can_view_dashboard,
                    "can_manage_tenants": limited_admin.can_manage_tenants,
                    "can_manage_subscriptions": limited_admin.can_manage_subscriptions,
                    "can_manage_payment_gateways": limited_admin.can_manage_payment_gateways,
                    "can_view_reports": limited_admin.can_view_reports,
                    "can_manage_communication": limited_admin.can_manage_communication,
                    "can_manage_settings": limited_admin.can_manage_settings,
                    "can_manage_limited_admins": limited_admin.can_manage_limited_admins,
                }
            }

        # Add supervisor permissions if user is a supervisor
        if self.user.role == Role.SUPERVISOR.value:
            try:
                sp = SupervisorPermission.objects.get(user=self.user)
                user_data["supervisor_permissions"] = {
                    "id": sp.id,
                    "is_active": sp.is_active,
                    "permissions": {
                        "can_manage_leads": sp.can_manage_leads,
                        "can_manage_deals": sp.can_manage_deals,
                        "can_manage_tasks": sp.can_manage_tasks,
                        "can_view_reports": sp.can_view_reports,
                        "can_manage_users": sp.can_manage_users,
                        "can_manage_products": sp.can_manage_products,
                        "can_manage_services": sp.can_manage_services,
                        "can_manage_real_estate": sp.can_manage_real_estate,
                        "can_manage_settings": sp.can_manage_settings,
                    },
                }
            except SupervisorPermission.DoesNotExist:
                user_data["supervisor_permissions"] = None
        else:
            user_data["supervisor_permissions"] = None

        data["user"] = user_data

        return data


def build_user_auth_payload(user, request=None):
    """
    Build user dict for login/impersonate responses.
    Matches the structure returned by CustomTokenObtainPairSerializer.
    """
    profile_photo = None
    if user.profile_photo:
        profile_photo = (
            request.build_absolute_uri(user.profile_photo.url)
            if request
            else user.profile_photo.url
        )
    user_data = {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone or "",
        "profile_photo": profile_photo,
        "role": user.role,
        "company": user.company.id if user.company else None,
        "company_name": user.company.name if user.company else None,
        "company_specialization": user.company.specialization if user.company else None,
        "is_active": user.is_active,
        "email_verified": user.email_verified,
        "phone_verified": getattr(user, "phone_verified", False),
        "is_superuser": user.is_superuser,
        "language": getattr(user, "language", None) or "ar",
    }
    try:
        limited_admin = LimitedAdmin.objects.select_related("user").get(user=user)
        user_data["limited_admin"] = {
            "id": limited_admin.id,
            "is_active": limited_admin.is_active,
            "permissions": {
                "can_view_dashboard": limited_admin.can_view_dashboard,
                "can_manage_tenants": limited_admin.can_manage_tenants,
                "can_manage_subscriptions": limited_admin.can_manage_subscriptions,
                "can_manage_payment_gateways": limited_admin.can_manage_payment_gateways,
                "can_view_reports": limited_admin.can_view_reports,
                "can_manage_communication": limited_admin.can_manage_communication,
                "can_manage_settings": limited_admin.can_manage_settings,
                "can_manage_limited_admins": limited_admin.can_manage_limited_admins,
            },
        }
    except LimitedAdmin.DoesNotExist:
        pass
    if user.role == Role.SUPERVISOR.value:
        try:
            sp = SupervisorPermission.objects.get(user=user)
            user_data["supervisor_permissions"] = {
                "id": sp.id,
                "is_active": sp.is_active,
                "permissions": {
                    "can_manage_leads": sp.can_manage_leads,
                    "can_manage_deals": sp.can_manage_deals,
                    "can_manage_tasks": sp.can_manage_tasks,
                    "can_view_reports": sp.can_view_reports,
                    "can_manage_users": sp.can_manage_users,
                    "can_manage_products": sp.can_manage_products,
                    "can_manage_services": sp.can_manage_services,
                    "can_manage_real_estate": sp.can_manage_real_estate,
                    "can_manage_settings": sp.can_manage_settings,
                },
            }
        except SupervisorPermission.DoesNotExist:
            user_data["supervisor_permissions"] = None
    else:
        user_data["supervisor_permissions"] = None
    return user_data


class ImpersonateSerializer(serializers.Serializer):
    """
    Serializer for super admin impersonation.
    Accepts either user_id (company owner) or company_id to impersonate that company's owner.
    """

    user_id = serializers.IntegerField(required=False, allow_null=True)
    company_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        user_id = attrs.get("user_id")
        company_id = attrs.get("company_id")
        if not user_id and not company_id:
            raise serializers.ValidationError(
                "Provide either user_id or company_id."
            )
        if user_id and company_id:
            raise serializers.ValidationError(
                "Provide only one of user_id or company_id."
            )
        if company_id:
            try:
                company = Company.objects.select_related("owner").get(pk=company_id)
            except Company.DoesNotExist:
                raise serializers.ValidationError("Company not found.")
            if not company.owner_id:
                raise serializers.ValidationError(
                    "Company has no owner assigned."
                )
            attrs["target_user"] = company.owner
            attrs["company"] = company
            return attrs
        # user_id path
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found.")
        if user.is_superuser:
            raise serializers.ValidationError("Cannot impersonate a super admin.")
        if not Company.objects.filter(owner=user).exists():
            raise serializers.ValidationError(
                "User is not a company owner. Only company owners can be impersonated."
            )
        attrs["target_user"] = user
        attrs["company"] = Company.objects.filter(owner=user).first()
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for changing user password"""
    current_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate(self, attrs):
        """Validate that new_password and confirm_password match"""
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({
                'confirm_password': 'New password and confirm password do not match.'
            })
        return attrs

    def validate_new_password(self, value):
        """Validate new password using Django's password validators"""
        user = self.context['request'].user
        try:
            validate_password(value, user)
        except ValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value


class RegisterCompanySerializer(serializers.Serializer):
    """Serializer for company registration"""
    company = serializers.DictField(
        child=serializers.CharField(),
        required=True
    )
    owner = serializers.DictField(
        child=serializers.CharField(),
        required=True
    )
    phone_verification_token = serializers.CharField(required=False, allow_blank=True, write_only=True)
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'yearly'],
        required=False,
        default='monthly'
    )

    def _request_can_skip_phone_verification(self, request):
        if not request:
            return False
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if getattr(u, "is_superuser", False) or u.is_super_admin():
            return True
        try:
            la = u.limited_admin_profile
            return la.is_active and la.can_manage_tenants
        except Exception:
            return False

    def validate(self, attrs):
        from .phone_otp_utils import (
            BadSignature,
            SignatureExpired,
            unsign_phone_registration_token,
        )
        from .platform_whatsapp import normalize_phone_digits
        from django.core.cache import cache

        request = self.context.get("request")
        token = (attrs.get("phone_verification_token") or "").strip()
        owner = attrs.get("owner") or {}
        phone = normalize_phone_digits(owner.get("phone", ""))
        if not phone:
            raise serializers.ValidationError({"owner": ["Phone number is required"]})
        # Source of truth is runtime admin toggle only; default is disabled.
        otp_required = bool(cache.get("platform_whatsapp_otp_required_override", False))
        if not otp_required:
            attrs["_skip_phone_verification"] = True
            return attrs
        if not token:
            if request and self._request_can_skip_phone_verification(request):
                attrs["_skip_phone_verification"] = True
                return attrs
            raise serializers.ValidationError(
                {"phone_verification_token": ["WhatsApp verification is required before registration."]}
            )
        try:
            signed_phone = unsign_phone_registration_token(token, max_age=1800)
        except SignatureExpired:
            raise serializers.ValidationError(
                {"phone_verification_token": ["Verification expired. Verify your phone again."]}
            )
        except BadSignature:
            raise serializers.ValidationError(
                {"phone_verification_token": ["Invalid verification token."]}
            )
        if signed_phone != phone:
            raise serializers.ValidationError(
                {"phone_verification_token": ["Phone does not match verification."]}
            )
        return attrs

    def validate_company(self, value):
        """Validate company data"""
        required_fields = ['name', 'domain', 'specialization']
        for field in required_fields:
            if field not in value:
                raise serializers.ValidationError(f"Company {field} is required")
        
        if value['specialization'] not in ['real_estate', 'services', 'products']:
            raise serializers.ValidationError("Invalid specialization")
        
        # Check if domain already exists
        if Company.objects.filter(domain=value['domain']).exists():
            raise serializers.ValidationError("Company with this domain already exists")
        
        return value

    def validate_owner(self, value):
        """Validate owner data"""
        required_fields = ['first_name', 'last_name', 'email', 'username', 'password', 'phone']
        for field in required_fields:
            if field not in value:
                raise serializers.ValidationError(f"Owner {field} is required")
        
        # Check if username already exists
        if User.objects.filter(username=value['username']).exists():
            raise serializers.ValidationError("Username already exists")
        
        # Check if email already exists
        if User.objects.filter(email=value['email']).exists():
            raise serializers.ValidationError("Email already exists")

        from .platform_whatsapp import normalize_phone_digits

        phone = normalize_phone_digits(value.get("phone", "").strip())
        if not phone:
            raise serializers.ValidationError("Phone number is required")
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError("Phone number already exists")
        value["phone"] = phone
        
        # Validate password
        try:
            validate_password(value['password'])
        except ValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        
        return value

    def create(self, validated_data):
        """Create company, owner user, and subscription"""
        validated_data.pop("phone_verification_token", None)
        validated_data.pop("_skip_phone_verification", None)
        company_data = validated_data['company']
        owner_data = validated_data['owner']
        plan_id = validated_data.get('plan_id')
        billing_cycle = validated_data.get('billing_cycle', 'monthly')

        # Create owner user
        owner = User.objects.create_user(
            username=owner_data['username'],
            email=owner_data['email'],
            password=owner_data['password'],
            first_name=owner_data['first_name'],
            last_name=owner_data['last_name'],
            phone=owner_data['phone'],
            role=Role.ADMIN.value,
        )
        owner.phone_verified = True
        owner.save(update_fields=["phone_verified"])

        # Create company
        company = Company.objects.create(
            name=company_data['name'],
            domain=company_data['domain'],
            specialization=company_data['specialization'],
            owner=owner,
        )

        if company_data["specialization"] in ("real_estate", "services"):
            from settings.lead_status_automation import ensure_visited_lead_status

            ensure_visited_lead_status(company)

        # Link company to owner
        owner.company = company
        owner.save()

        # Create subscription if plan is provided
        subscription = None
        requires_payment = False
        if plan_id:
            try:
                plan = Plan.objects.get(id=plan_id)
                # Free/trial plans never require payment and do not have billing cycles.
                # - Free: price_monthly==0 and price_yearly==0 and trial_days==0 -> no expiry (we store far-future end_date)
                # - Trial: price_monthly==0 and price_yearly==0 and trial_days>0 -> expires after trial_days
                is_free_or_trial = float(plan.price_monthly) <= 0 and float(plan.price_yearly) <= 0
                requires_payment = False
                if not is_free_or_trial:
                    # Check if plan requires payment for selected billing cycle
                    price = plan.price_yearly if billing_cycle == 'yearly' else plan.price_monthly
                    requires_payment = price > 0
                
                # Create subscription first to get the actual start_date
                # Then calculate end_date based on start_date and billing cycle
                subscription = Subscription.objects.create(
                    company=company,
                    plan=plan,
                    end_date=timezone.now(),  # Temporary, will update below
                    is_active=not requires_payment,  # Inactive if payment required
                )
                
                # Calculate end_date using the actual start_date
                if is_free_or_trial:
                    if int(getattr(plan, "trial_days", 0) or 0) > 0:
                        end_date = subscription.start_date + timedelta(days=int(plan.trial_days))
                    else:
                        # Treat "free forever" as a far-future end_date (Subscription.end_date is non-nullable)
                        end_date = subscription.start_date + timedelta(days=365 * 100)
                else:
                    if billing_cycle == 'yearly':
                        end_date = subscription.start_date + timedelta(days=365)
                    else:
                        end_date = subscription.start_date + timedelta(days=30)
                
                subscription.end_date = end_date
                subscription.current_period_start = subscription.start_date
                subscription.billing_cycle = (
                    BillingCycle.YEARLY if billing_cycle == "yearly" else BillingCycle.MONTHLY
                )
                if is_free_or_trial:
                    td = int(getattr(plan, "trial_days", 0) or 0)
                    subscription.subscription_status = (
                        SubscriptionStatus.TRIALING if td > 0 else SubscriptionStatus.ACTIVE
                    )
                else:
                    subscription.subscription_status = SubscriptionStatus.ACTIVE
                subscription.save(
                    update_fields=[
                        "end_date",
                        "current_period_start",
                        "billing_cycle",
                        "subscription_status",
                    ]
                )
            except Plan.DoesNotExist:
                pass  # If plan doesn't exist, continue without subscription

        return {
            'company': company,
            'owner': owner,
            'subscription': subscription,
            'requires_payment': requires_payment,
        }


class EmailVerificationSerializer(serializers.Serializer):
    """Serializer to verify email via code or token"""
    email = serializers.EmailField()
    code = serializers.CharField(required=False, allow_blank=True, max_length=6)
    token = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate_email(self, value):
        """Normalize email"""
        return value.strip().lower() if value else value

    def validate_code(self, value):
        """Normalize code - strip whitespace and return None if empty"""
        if value:
            value = value.strip()
            return value if value else None
        return None

    def validate_token(self, value):
        """Normalize token - strip whitespace and return None if empty"""
        if value:
            value = value.strip()
            return value if value else None
        return None

    def validate(self, attrs):
        email = attrs.get("email")
        code = attrs.get("code")
        token = attrs.get("token")

        if not code and not token:
            raise serializers.ValidationError({"code": "Either code or token must be provided."})

        try:
            attrs["user"] = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise serializers.ValidationError({"email": "User with this email does not exist."})

        return attrs


class RegistrationAvailabilitySerializer(serializers.Serializer):
    """Serializer to check availability of registration fields"""
    company_domain = serializers.CharField(required=False, allow_blank=False)
    email = serializers.EmailField(required=False)
    username = serializers.CharField(required=False, allow_blank=False)
    phone = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs):
        if not any(attrs.get(field) for field in ['company_domain', 'email', 'username', 'phone']):
            raise serializers.ValidationError("Provide at least one field to check.")

        errors = {}

        domain = attrs.get('company_domain')
        if domain:
            if Company.objects.filter(domain__iexact=domain.strip()).exists():
                errors['company_domain'] = "Company domain already exists"

        email = attrs.get('email')
        if email:
            if User.objects.filter(email__iexact=email.strip()).exists():
                errors['email'] = "Email already exists"

        username = attrs.get('username')
        if username:
            if User.objects.filter(username__iexact=username.strip()).exists():
                errors['username'] = "Username already exists"

        phone = attrs.get("phone")
        if phone:
            from .platform_whatsapp import normalize_phone_digits

            pn = normalize_phone_digits(phone.strip())
            if pn and User.objects.filter(phone=pn).exists():
                errors["phone"] = "Phone number already exists"

        if errors:
            raise serializers.ValidationError(errors)

        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    """Serializer for requesting password reset"""
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        """Normalize email"""
        return value.strip().lower() if value else value

    def validate(self, attrs):
        email = attrs.get("email")
        try:
            attrs["user"] = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # Don't reveal if email exists or not for security
            pass
        return attrs


class ResetPasswordSerializer(serializers.Serializer):
    """Serializer for resetting password using code or token"""
    email = serializers.EmailField(required=True)
    code = serializers.CharField(required=False, allow_blank=True, max_length=6)
    token = serializers.CharField(required=False, allow_blank=True, max_length=64)
    new_password = serializers.CharField(required=True, write_only=True)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate_email(self, value):
        """Normalize email"""
        return value.strip().lower() if value else value

    def validate_code(self, value):
        """Normalize code"""
        if value:
            value = value.strip()
            return value if value else None
        return None

    def validate_token(self, value):
        """Normalize token"""
        if value:
            value = value.strip()
            return value if value else None
        return None

    def validate(self, attrs):
        email = attrs.get("email")
        code = attrs.get("code")
        token = attrs.get("token")
        new_password = attrs.get("new_password")
        confirm_password = attrs.get("confirm_password")

        if not code and not token:
            raise serializers.ValidationError({"code": "Either code or token must be provided."})

        if new_password != confirm_password:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        try:
            attrs["user"] = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise serializers.ValidationError({"email": "User with this email does not exist."})

        # Validate new password
        try:
            validate_password(new_password, attrs["user"])
        except ValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})

        return attrs


class RequestTwoFactorAuthSerializer(serializers.Serializer):
    """Serializer for requesting 2FA code"""
    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate_username(self, value):
        """Normalize username - can be username or email"""
        return value.strip() if value else value

    def validate(self, attrs):
        username_or_email = attrs.get("username", "").strip()
        password = attrs.get("password", "")
        user = None

        # Try to find user by email or username
        if '@' in username_or_email:
            try:
                user = User.objects.get(email__iexact=username_or_email)
            except User.DoesNotExist:
                raise serializers.ValidationError({"error": "Invalid credentials."})
        else:
            try:
                user = User.objects.get(username__iexact=username_or_email)
            except User.DoesNotExist:
                raise serializers.ValidationError({"error": "Invalid credentials."})

        if not user.is_active:
            raise serializers.ValidationError({"error": "User account is inactive."})

        # Validate password
        if not user.check_password(password):
            raise serializers.ValidationError({"error": "Invalid credentials."})

        attrs["user"] = user
        return attrs


class VerifyTwoFactorAuthSerializer(serializers.Serializer):
    """Serializer for verifying 2FA code"""
    username = serializers.CharField(required=True)
    code = serializers.CharField(required=False, allow_blank=True, max_length=6)
    token = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate_username(self, value):
        """Normalize username - can be username or email"""
        return value.strip() if value else value

    def validate_code(self, value):
        """Normalize code"""
        if value:
            value = value.strip()
            return value if value else None
        return None

    def validate_token(self, value):
        """Normalize token"""
        if value:
            value = value.strip()
            return value if value else None
        return None

    def validate(self, attrs):
        username_or_email = attrs.get("username", "").strip()
        code = attrs.get("code")
        token = attrs.get("token")

        if not code and not token:
            raise serializers.ValidationError({"code": "Either code or token must be provided."})

        # Find user
        user = None
        if '@' in username_or_email:
            try:
                user = User.objects.get(email__iexact=username_or_email)
            except User.DoesNotExist:
                raise serializers.ValidationError({"username": "User not found."})
        else:
            try:
                user = User.objects.get(username__iexact=username_or_email)
            except User.DoesNotExist:
                raise serializers.ValidationError({"username": "User not found."})

        attrs["user"] = user
        return attrs


class LimitedAdminSerializer(serializers.ModelSerializer):
    """Serializer for LimitedAdmin model"""
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_superuser=False),
        source='user',
        write_only=True
    )
    user = serializers.SerializerMethodField(read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = LimitedAdmin
        fields = [
            'id',
            'user',
            'user_id',
            'is_active',
            'created_by',
            'created_by_username',
            'created_at',
            'updated_at',
            'can_view_dashboard',
            'can_manage_tenants',
            'can_manage_subscriptions',
            'can_manage_payment_gateways',
            'can_view_reports',
            'can_manage_communication',
            'can_manage_settings',
            'can_manage_limited_admins',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']
    
    def get_user(self, obj):
        """Return user details"""
        if not obj.user:
            return None
        return {
            'id': obj.user.id,
            'username': obj.user.username,
            'email': obj.user.email,
            'first_name': obj.user.first_name,
            'last_name': obj.user.last_name,
        }
    
    def create(self, validated_data):
        """Create LimitedAdmin and set created_by"""
        request = self.context.get('request')
        if request and request.user:
            validated_data['created_by'] = request.user
        return super().create(validated_data)
    
    def validate_user_id(self, value):
        """Ensure user is not a superuser and doesn't already have a LimitedAdmin profile"""
        if value.is_superuser:
            raise serializers.ValidationError("Superusers cannot be limited admins.")
        
        if LimitedAdmin.objects.filter(user=value).exists():
            raise serializers.ValidationError("This user already has a limited admin profile.")
        
        return value


class CreateLimitedAdminSerializer(serializers.Serializer):
    """Serializer for creating a new user and LimitedAdmin"""
    username = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    is_active = serializers.BooleanField(default=True)
    can_view_dashboard = serializers.BooleanField(default=False)
    can_manage_tenants = serializers.BooleanField(default=False)
    can_manage_subscriptions = serializers.BooleanField(default=False)
    can_manage_payment_gateways = serializers.BooleanField(default=False)
    can_view_reports = serializers.BooleanField(default=False)
    can_manage_communication = serializers.BooleanField(default=False)
    can_manage_settings = serializers.BooleanField(default=False)
    can_manage_limited_admins = serializers.BooleanField(default=False)
    
    def validate_username(self, value):
        """Check if username already exists"""
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Username already exists.")
        return value
    
    def validate_email(self, value):
        """Check if email already exists"""
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already exists.")
        return value
    
    def validate_password(self, value):
        """Validate password"""
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value
    
    def create(self, validated_data):
        """Create user and LimitedAdmin"""
        password = validated_data.pop('password')
        # created_by is passed from view's save(created_by=...) and must not be passed to User
        created_by = validated_data.pop('created_by', None)
        permissions = {
            'is_active': validated_data.pop('is_active', True),
            'can_view_dashboard': validated_data.pop('can_view_dashboard', False),
            'can_manage_tenants': validated_data.pop('can_manage_tenants', False),
            'can_manage_subscriptions': validated_data.pop('can_manage_subscriptions', False),
            'can_manage_payment_gateways': validated_data.pop('can_manage_payment_gateways', False),
            'can_view_reports': validated_data.pop('can_view_reports', False),
            'can_manage_communication': validated_data.pop('can_manage_communication', False),
            'can_manage_settings': validated_data.pop('can_manage_settings', False),
            'can_manage_limited_admins': validated_data.pop('can_manage_limited_admins', False),
        }
        
        # Create user (validated_data now only has username, email, first_name, last_name)
        user = User.objects.create_user(
            password=password,
            is_staff=True,  # Allow access to admin panel
            **validated_data
        )
        
        # Create LimitedAdmin
        limited_admin = LimitedAdmin.objects.create(
            user=user,
            created_by=created_by,
            **permissions
        )
        
        return limited_admin


class SupervisorSerializer(serializers.ModelSerializer):
    """Serializer for SupervisorPermission model (read/update)."""
    user = serializers.SerializerMethodField(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=Role.SUPERVISOR.value),
        source='user',
        write_only=True,
        required=False
    )

    class Meta:
        model = SupervisorPermission
        fields = [
            'id',
            'user',
            'user_id',
            'is_active',
            'created_at',
            'updated_at',
            'can_manage_leads',
            'can_manage_deals',
            'can_manage_tasks',
            'can_view_reports',
            'can_manage_users',
            'can_manage_products',
            'can_manage_services',
            'can_manage_real_estate',
            'can_manage_settings',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_user(self, obj):
        if not obj.user:
            return None
        return {
            'id': obj.user.id,
            'username': obj.user.username,
            'email': obj.user.email,
            'first_name': obj.user.first_name,
            'last_name': obj.user.last_name,
        }

    def update(self, instance, validated_data):
        company = getattr(instance.user, 'company', None)
        if company:
            perms = {k: validated_data.get(k, getattr(instance, k, False)) for k in INVENTORY_PERM_KEYS}
            filter_inventory_permissions_for_company(perms, company)
            for k in INVENTORY_PERM_KEYS:
                validated_data[k] = perms[k]
        return super().update(instance, validated_data)


class CreateSupervisorSerializer(serializers.Serializer):
    """Create a new user with role=supervisor and SupervisorPermission (company-scoped)."""
    username = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    is_active = serializers.BooleanField(default=True)
    can_manage_leads = serializers.BooleanField(default=False)
    can_manage_deals = serializers.BooleanField(default=False)
    can_manage_tasks = serializers.BooleanField(default=False)
    can_view_reports = serializers.BooleanField(default=False)
    can_manage_users = serializers.BooleanField(default=False)
    can_manage_products = serializers.BooleanField(default=False)
    can_manage_services = serializers.BooleanField(default=False)
    can_manage_real_estate = serializers.BooleanField(default=False)
    can_manage_settings = serializers.BooleanField(default=False)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Username already exists.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already exists.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value

    def create(self, validated_data):
        company = validated_data.pop('company')
        password = validated_data.pop('password')
        is_active = validated_data.pop('is_active', True)
        perms = {
            'can_manage_leads': validated_data.pop('can_manage_leads', False),
            'can_manage_deals': validated_data.pop('can_manage_deals', False),
            'can_manage_tasks': validated_data.pop('can_manage_tasks', False),
            'can_view_reports': validated_data.pop('can_view_reports', False),
            'can_manage_users': validated_data.pop('can_manage_users', False),
            'can_manage_products': validated_data.pop('can_manage_products', False),
            'can_manage_services': validated_data.pop('can_manage_services', False),
            'can_manage_real_estate': validated_data.pop('can_manage_real_estate', False),
            'can_manage_settings': validated_data.pop('can_manage_settings', False),
        }
        filter_inventory_permissions_for_company(perms, company)
        user = User.objects.create_user(
            password=password,
            role=Role.SUPERVISOR.value,
            company=company,
            **validated_data
        )
        sp = SupervisorPermission.objects.create(
            user=user,
            is_active=is_active,
            **perms
        )
        return sp
