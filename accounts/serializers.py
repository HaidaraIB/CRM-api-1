from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from drf_spectacular.utils import extend_schema_field
from .models import User, Role
from companies.models import Company
from subscriptions.models import Plan, Subscription
from datetime import datetime, timedelta


class UserSerializer(serializers.ModelSerializer):
    is_me = serializers.SerializerMethodField()
    company_name = serializers.CharField(source="company.name", read_only=True)
    company_specialization = serializers.CharField(source="company.specialization", read_only=True)
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
            "role",
            "company",
            "company_name",
            "company_specialization",
            "is_active",
            "is_staff",
            "is_superuser",
            "date_joined",
            "last_login",
            "is_me",
        ]
        read_only_fields = ["id", "date_joined", "last_login"]
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
            "role",
            "company",
            "company_name",
            "company_specialization",
            "is_active",
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

    def validate(self, attrs):
        data = super().validate(attrs)

        # Add user information to the response
        data["user"] = {
            "id": self.user.id,
            "username": self.user.username,
            "email": self.user.email,
            "first_name": self.user.first_name,
            "last_name": self.user.last_name,
            "phone": self.user.phone or "",
            "role": self.user.role,
            "company": self.user.company.id if self.user.company else None,
            "company_name": self.user.company.name if self.user.company else None,
            "company_specialization": self.user.company.specialization if self.user.company else None,
            "is_active": self.user.is_active,
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
        required_fields = ['first_name', 'last_name', 'email', 'username', 'password']
        for field in required_fields:
            if field not in value:
                raise serializers.ValidationError(f"Owner {field} is required")
        
        # Check if username already exists
        if User.objects.filter(username=value['username']).exists():
            raise serializers.ValidationError("Username already exists")
        
        # Check if email already exists
        if User.objects.filter(email=value['email']).exists():
            raise serializers.ValidationError("Email already exists")
        
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
        if plan_id:
            try:
                plan = Plan.objects.get(id=plan_id)
                # Calculate end date based on billing cycle
                if billing_cycle == 'yearly':
                    end_date = datetime.now() + timedelta(days=365)
                else:
                    end_date = datetime.now() + timedelta(days=30)
                
                subscription = Subscription.objects.create(
                    company=company,
                    plan=plan,
                    end_date=end_date,
                    is_active=True,
                    auto_renew=True,
                )
            except Plan.DoesNotExist:
                pass  # If plan doesn't exist, continue without subscription

        return {
            'company': company,
            'owner': owner,
            'subscription': subscription,
        }
