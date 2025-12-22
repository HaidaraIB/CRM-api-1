from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from drf_spectacular.utils import extend_schema_field
from .models import User, Role, TwoFactorAuth
from companies.models import Company
from subscriptions.models import Plan, Subscription
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
            "date_joined",
            "last_login",
            "is_me",
        ]
        read_only_fields = ["id", "date_joined", "last_login", "email_verified"]
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
            "registration_completed": company.registration_completed,
            "registration_completed_at": company.registration_completed_at.isoformat() if company.registration_completed_at else None,
            "auto_assign_enabled": company.auto_assign_enabled,
            "re_assign_enabled": company.re_assign_enabled,
            "re_assign_hours": company.re_assign_hours,
        }
        
        if subscription:
            company_data["subscription"] = {
                "id": subscription.id,
                "plan": {
                    "id": subscription.plan.id,
                    "name": subscription.plan.name,
                    "name_ar": subscription.plan.name_ar if subscription.plan.name_ar else None,
                },
                "is_active": subscription.is_active,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "auto_renew": subscription.auto_renew,
            }
        else:
            company_data["subscription"] = None
        
        return company_data


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

        # Add user information to the response
        data["user"] = {
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
        }

        return data


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
    plan_id = serializers.IntegerField(required=False, allow_null=True)
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'yearly'],
        required=False,
        default='monthly'
    )

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

        phone = value.get('phone', '').strip()
        if not phone:
            raise serializers.ValidationError("Phone number is required")
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError("Phone number already exists")
        value['phone'] = phone
        
        # Validate password
        try:
            validate_password(value['password'])
        except ValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        
        return value

    def create(self, validated_data):
        """Create company, owner user, and subscription"""
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

        # Create company
        company = Company.objects.create(
            name=company_data['name'],
            domain=company_data['domain'],
            specialization=company_data['specialization'],
            owner=owner,
        )

        # Link company to owner
        owner.company = company
        owner.save()

        # Create subscription if plan is provided
        subscription = None
        requires_payment = False
        if plan_id:
            try:
                plan = Plan.objects.get(id=plan_id)
                # Check if plan requires payment
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
                
                # Calculate end_date based on billing cycle using the actual start_date
                if billing_cycle == 'yearly':
                    end_date = subscription.start_date + timedelta(days=365)
                else:
                    end_date = subscription.start_date + timedelta(days=30)
                
                # Update subscription with correct end_date
                subscription.end_date = end_date
                subscription.save(update_fields=['end_date'])
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

        phone = attrs.get('phone')
        if phone:
            if User.objects.filter(phone=phone.strip()).exists():
                errors['phone'] = "Phone number already exists"

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

    def validate_username(self, value):
        """Normalize username - can be username or email"""
        return value.strip() if value else value

    def validate(self, attrs):
        username_or_email = attrs.get("username", "").strip()
        user = None

        # Try to find user by email or username
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

        if not user.is_active:
            raise serializers.ValidationError({"username": "User account is inactive."})

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
