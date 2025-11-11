from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from drf_spectacular.utils import extend_schema_field
from .models import User


class UserSerializer(serializers.ModelSerializer):
    is_me = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "company",
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
    is_me = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "company",
            "company_name",
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
            "role": self.user.role,
            "company": self.user.company.id if self.user.company else None,
            "company_name": self.user.company.name if self.user.company else None,
            "is_active": self.user.is_active,
        }

        return data
