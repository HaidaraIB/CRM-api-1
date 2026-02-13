from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.db.models import Q
from enum import Enum
from datetime import timedelta
import uuid
import secrets


class Role(Enum):
    ADMIN = "admin"
    EMPLOYEE = "employee"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class User(AbstractUser):
    email = models.EmailField(unique=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, null=True, blank=True
    )
    role = models.CharField(max_length=64, choices=Role.choices())
    phone = models.CharField(max_length=20, blank=True, null=True)
    profile_photo = models.ImageField(upload_to="profile_photos/", null=True, blank=True)
    email_verified = models.BooleanField(default=False)
    fcm_token = models.CharField(max_length=255, blank=True, null=True, help_text="Firebase Cloud Messaging token for push notifications")
    language = models.CharField(max_length=10, default='ar', choices=[('ar', 'Arabic'), ('en', 'English')], help_text="User preferred language for notifications")

    def __str__(self):
        return self.username

    def is_super_admin(self):
        return self.is_superuser

    def is_admin(self):
        return self.role == Role.ADMIN.value

    def is_employee(self):
        return self.role == Role.EMPLOYEE.value

    def has_role(self, role):
        return self.role == role

    def can_access_user(self, user):
        if self == user:
            return True
        if self.is_admin() and self.company == user.company:
            return True
        return False

    def can_access_company_data(self, company):
        return self.company == company

    class Meta:
        db_table = "users"
        permissions = [
            ("view_all_users", "Can view all users"),
            ("manage_all_users", "Can manage all users"),
            ("manage_company_users", "Can manage company users"),
            ("view_company_data", "Can view company data"),
            ("manage_company_data", "Can manage company data"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["phone"],
                condition=Q(phone__isnull=False) & ~Q(phone=""),
                name="unique_user_phone_not_null",
            ),
        ]


class EmailVerification(models.Model):
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="email_verifications",
    )
    code = models.CharField(max_length=6)
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "email_verifications"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_verified=False),
                name="unique_pending_verification_per_user",
            )
        ]

    def __str__(self):
        return f"{self.user.email} verification"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @classmethod
    def create_for_user(cls, user, expiry_hours: int = 48):
        cls.objects.filter(user=user, is_verified=False).delete()
        code = f"{secrets.randbelow(900000) + 100000}"
        token = uuid.uuid4().hex
        expires_at = timezone.now() + timedelta(hours=expiry_hours)
        return cls.objects.create(
            user=user,
            code=code,
            token=token,
            expires_at=expires_at,
        )

    def mark_verified(self):
        self.is_verified = True
        self.verified_at = timezone.now()
        self.save(update_fields=["is_verified", "verified_at"])


class PasswordReset(models.Model):
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="password_resets",
    )
    code = models.CharField(max_length=6)
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "password_resets"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} password reset"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @classmethod
    def create_for_user(cls, user, expiry_hours: int = 1):
        """Create a new password reset token for user, delete old unused ones"""
        cls.objects.filter(user=user, is_used=False).delete()
        code = f"{secrets.randbelow(900000) + 100000}"
        token = uuid.uuid4().hex
        expires_at = timezone.now() + timedelta(hours=expiry_hours)
        return cls.objects.create(
            user=user,
            code=code,
            token=token,
            expires_at=expires_at,
        )

    def mark_used(self):
        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=["is_used", "used_at"])


class TwoFactorAuth(models.Model):
    """Two-Factor Authentication code for login"""
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="two_factor_auths",
    )
    code = models.CharField(max_length=6)
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "two_factor_auths"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} 2FA"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @classmethod
    def create_for_user(cls, user, expiry_minutes: int = 10):
        """Create a new 2FA code for user, delete old unused ones"""
        cls.objects.filter(user=user, is_verified=False).delete()
        code = f"{secrets.randbelow(900000) + 100000}"
        token = uuid.uuid4().hex
        expires_at = timezone.now() + timedelta(minutes=expiry_minutes)
        return cls.objects.create(
            user=user,
            code=code,
            token=token,
            expires_at=expires_at,
        )

    def mark_verified(self):
        self.is_verified = True
        self.verified_at = timezone.now()
        self.save(update_fields=["is_verified", "verified_at"])


class LimitedAdmin(models.Model):
    """Limited Admin for Super Admin Panel with restricted permissions"""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="limited_admin_profile",
        limit_choices_to={'is_superuser': False}
    )
    is_active = models.BooleanField(default=True, help_text="Whether this limited admin can access the panel")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_limited_admins",
        limit_choices_to={'is_superuser': True}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Permissions
    can_view_dashboard = models.BooleanField(default=False)
    can_manage_tenants = models.BooleanField(default=False)
    can_manage_subscriptions = models.BooleanField(default=False)
    can_manage_payment_gateways = models.BooleanField(default=False)
    can_view_reports = models.BooleanField(default=False)
    can_manage_communication = models.BooleanField(default=False)
    can_manage_settings = models.BooleanField(default=False)
    can_manage_limited_admins = models.BooleanField(default=False, help_text="Can manage other limited admins")
    
    class Meta:
        db_table = "limited_admins"
        ordering = ["-created_at"]
        verbose_name = "Limited Admin"
        verbose_name_plural = "Limited Admins"
    
    def __str__(self):
        return f"Limited Admin: {self.user.username}"
    
    def has_permission(self, permission_name: str) -> bool:
        """Check if this limited admin has a specific permission"""
        if not self.is_active:
            return False
        
        permission_map = {
            'view_dashboard': self.can_view_dashboard,
            'manage_tenants': self.can_manage_tenants,
            'manage_subscriptions': self.can_manage_subscriptions,
            'manage_payment_gateways': self.can_manage_payment_gateways,
            'view_reports': self.can_view_reports,
            'manage_communication': self.can_manage_communication,
            'manage_settings': self.can_manage_settings,
            'manage_limited_admins': self.can_manage_limited_admins,
        }
        
        return permission_map.get(permission_name, False)