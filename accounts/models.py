from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.db.models import Q
from enum import Enum
from datetime import timedelta
import uuid
import secrets


class Role(Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    EMPLOYEE = "employee"
    DATA_ENTRY = "data_entry"
    RECEPTION = "reception"
    DOCTOR = "doctor"
    CALL_CENTER = "call_center"

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
    phone_verified = models.BooleanField(
        default=False,
        help_text="Owner phone verified via WhatsApp OTP before registration (or legacy migration).",
    )
    fcm_token = models.CharField(max_length=255, blank=True, null=True, help_text="Firebase Cloud Messaging token for push notifications")
    fcm_tokens = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Firebase Cloud Messaging tokens for multi-device push notifications",
    )
    language = models.CharField(max_length=10, default='ar', choices=[('ar', 'Arabic'), ('en', 'English')], help_text="User preferred language for notifications")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_source = models.CharField(
        max_length=16,
        choices=[
            ("web", "Web"),
            ("mobile", "Mobile"),
            ("unknown", "Unknown"),
        ],
        default="unknown",
    )
    # Crediting cursor for measured CRM usage time. Lives here rather than on
    # WorkDaySummary because the first ping of a new local day has no day row yet.
    work_last_ping_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC timestamp of the last credited work-session ping (crediting cursor).",
    )
    # Monday=0 .. Sunday=6 (datetime.weekday); null = no fixed weekly day off
    weekly_day_off = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Weekly day off: 0=Monday .. 6=Sunday. Null means no recurring weekly off.",
    )
    # Daily working-hours window (company timezone). Both null = no shift (excluded from urgent pool).
    work_start_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Daily work start time (company timezone). Must be set together with work_end_time.",
    )
    work_end_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Daily work end time (company timezone). Must be set together with work_start_time.",
    )
    # Temporary unavailability, on top of weekly_day_off / work hours. Both self-expiring:
    # the user keeps full access, they are only skipped by lead/arrival routing.
    # Planned leave, inclusive, in company-local dates. Set/cleared together.
    time_off_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="First day of time off (company timezone, inclusive). Set together with time_off_end_date.",
    )
    time_off_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Last day of time off (company timezone, inclusive). Set together with time_off_start_date.",
    )
    # Short ad-hoc absence ("stepping out"), expires on its own.
    unavailable_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When set and in the future, user receives no new lead assignments or arrival routing.",
    )
    whatsapp_call_away_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When set and in the future, user is Away for WhatsApp Cloud Calling (no incoming rings).",
    )
    login_two_factor_enabled = models.BooleanField(
        default=True,
        help_text="When enabled, company owner must complete email 2FA at login (unless trusted device).",
    )
    failed_login_attempts = models.PositiveIntegerField(
        default=0,
        help_text="Consecutive failed password attempts at login; reset on success or lockout.",
    )
    lockout_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When set in the future, login is rejected until this time (temporary lockout).",
    )
    can_delete_clients = models.BooleanField(
        default=False,
        help_text="When True, employee/supervisor may delete clients (customers).",
    )
    whatsapp_chat_enabled = models.BooleanField(
        default=True,
        help_text="When False, this employee cannot access WhatsApp chats.",
    )
    whatsapp_call_enabled = models.BooleanField(
        default=True,
        help_text="When False, this employee cannot access WhatsApp calling.",
    )

    def __str__(self):
        return self.username

    def is_super_admin(self):
        return self.is_superuser

    def is_admin(self):
        return self.role == Role.ADMIN.value

    def is_employee(self):
        return self.role == Role.EMPLOYEE.value

    def is_doctor(self):
        return self.role == Role.DOCTOR.value

    def is_reception(self):
        return self.role == Role.RECEPTION.value

    def is_assigned_clinical_staff(self):
        """Sales employee or clinic doctor: scoped to assigned clients."""
        return self.is_employee() or self.is_doctor()

    def is_data_entry(self):
        return self.role == Role.DATA_ENTRY.value

    def is_call_center(self):
        return self.role == Role.CALL_CENTER.value

    def is_supervisor(self):
        return self.role == Role.SUPERVISOR.value

    def requires_campaign_approval(self) -> bool:
        """
        True for every staff role except Owner (admin), Super Admin, and Supervisor.
        These roles may only submit Messaging Center bulk-send requests scoped to
        their own assigned leads, subject to owner approval before anything sends.
        """
        return self.role not in (
            Role.ADMIN.value,
            Role.SUPER_ADMIN.value,
            Role.SUPERVISOR.value,
        )

    def has_role(self, role):
        return self.role == role

    def can_access_user(self, user):
        if self == user:
            return True
        if self.is_admin() and self.company == user.company:
            return True
        if self.is_supervisor() and self.company == user.company:
            try:
                return self.supervisor_permissions.can_manage_users
            except Exception:
                return False
        return False

    def can_access_company_data(self, company):
        return self.can_access_tenant_company_data(company)

    def can_access_tenant_company_data(self, company):
        """
        Tenant CRM access is always company-scoped.
        `is_superuser` is reserved for platform/admin-panel capabilities.
        """
        return self.company == company

    def supervisor_has_permission(self, permission_name: str) -> bool:
        """Return True if user is an active supervisor with the given permission."""
        if not self.is_supervisor():
            return False
        try:
            sp = self.supervisor_permissions
            return sp.is_active and sp.has_permission(permission_name)
        except Exception:
            return False

    @staticmethod
    def _normalize_fcm_token(token):
        if not isinstance(token, str):
            return ""
        return token.strip()

    def _normalized_fcm_tokens(self):
        raw_tokens = self.fcm_tokens if isinstance(self.fcm_tokens, list) else []
        seen = set()
        normalized = []
        for token in raw_tokens:
            normalized_token = self._normalize_fcm_token(token)
            if not normalized_token or normalized_token in seen:
                continue
            seen.add(normalized_token)
            normalized.append(normalized_token)
        return normalized

    def iter_fcm_tokens_for_push(self, platform=None):
        """
        Tokens to deliver a push to.

        With no ``platform`` this returns exactly what it always did — every token
        on the user — so every existing caller is unaffected.

        Passing a platform narrows delivery to devices registered as that kind,
        which is what stops a browser-only event buzzing someone's phone at night.
        Legacy tokens are deliberately excluded from a narrowed query: they predate
        device registration and their platform is genuinely unknown, so treating
        them as a match would defeat the filter.
        """
        if platform:
            return list(
                self.devices.filter(platform=platform)
                .order_by("-last_seen_at")
                .values_list("token", flat=True)
            )

        tokens = self._normalized_fcm_tokens()
        legacy_token = self._normalize_fcm_token(self.fcm_token)
        if legacy_token and legacy_token not in tokens:
            tokens.append(legacy_token)
        return tokens

    def has_any_fcm_token(self):
        return bool(self.iter_fcm_tokens_for_push())

    def add_fcm_token(self, token):
        normalized_token = self._normalize_fcm_token(token)
        if not normalized_token:
            return False
        tokens = self._normalized_fcm_tokens()
        if normalized_token in tokens:
            self.fcm_tokens = tokens
            return False
        tokens.append(normalized_token)
        self.fcm_tokens = tokens
        return True

    def remove_fcm_token(self, token):
        normalized_token = self._normalize_fcm_token(token)
        if not normalized_token:
            return False
        current_tokens = self._normalized_fcm_tokens()
        tokens = [t for t in current_tokens if t != normalized_token]
        changed = len(tokens) != len(current_tokens)
        self.fcm_tokens = tokens
        if self.fcm_token == normalized_token:
            self.fcm_token = None
            changed = True
        return changed

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


class PhoneRegistrationChallenge(models.Model):
    """Pre-registration WhatsApp OTP state (no user row yet)."""

    phone_normalized = models.CharField(max_length=32, db_index=True)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "phone_registration_challenges"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone_normalized", "-created_at"]),
        ]

    def __str__(self):
        return f"phone challenge {self.phone_normalized}"


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


class OwnerTrustedDevice(models.Model):
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="owner_trusted_devices",
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    user_agent_hash = models.CharField(max_length=64, blank=True, default="")
    ip_address = models.CharField(max_length=64, blank=True, default="")
    trusted_until = models.DateTimeField()
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "owner_trusted_devices"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "trusted_until"]),
            models.Index(fields=["user", "revoked_at"]),
        ]

    def __str__(self):
        return f"OwnerTrustedDevice user={self.user_id}"


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
    can_manage_content = models.BooleanField(
        default=False,
        help_text="Can manage Loop user guide articles and news posts",
    )
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
            'manage_content': self.can_manage_content,
            'manage_settings': self.can_manage_settings,
            'manage_limited_admins': self.can_manage_limited_admins,
        }
        
        return permission_map.get(permission_name, False)


class SupervisorPermission(models.Model):
    """
    Supervisor permissions within a company (CRM).
    Only users with role=supervisor have this profile; company admin grants/revokes permissions.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="supervisor_permissions",
        limit_choices_to={'role': Role.SUPERVISOR.value}
    )
    is_active = models.BooleanField(default=True, help_text="Whether this supervisor can access the CRM with granted permissions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # CRM permissions (same pattern as Limited Admin)
    can_manage_leads = models.BooleanField(default=False)
    can_manage_deals = models.BooleanField(default=False)
    can_manage_tasks = models.BooleanField(default=False)
    can_view_reports = models.BooleanField(default=False)
    can_manage_users = models.BooleanField(default=False)
    can_manage_products = models.BooleanField(default=False)
    can_manage_services = models.BooleanField(default=False)
    can_manage_real_estate = models.BooleanField(default=False)
    can_manage_settings = models.BooleanField(default=False)
    can_manage_whatsapp_chats = models.BooleanField(default=True)
    can_manage_whatsapp_calls = models.BooleanField(default=True)

    # Team-activity notification toggles (owner-controlled, mirrors the 3 categories
    # the owner already gets via NotificationSettings.notification_types).
    notify_team_activity_status = models.BooleanField(
        default=False,
        help_text="Notify this supervisor when a lead's status changes",
    )
    notify_team_activity_action = models.BooleanField(
        default=False,
        help_text="Notify this supervisor about team actions (calls, visits, tasks, deals won)",
    )
    notify_team_activity_overdue = models.BooleanField(
        default=False,
        help_text="Notify this supervisor about overdue / no-follow-up digests",
    )

    class Meta:
        db_table = "supervisor_permissions"
        ordering = ["-created_at"]
        verbose_name = "Supervisor Permission"
        verbose_name_plural = "Supervisor Permissions"

    def __str__(self):
        return f"Supervisor: {self.user.username}"

    def has_permission(self, permission_name: str) -> bool:
        if not self.is_active:
            return False
        permission_map = {
            "manage_leads": self.can_manage_leads,
            "manage_deals": self.can_manage_deals,
            "manage_tasks": self.can_manage_tasks,
            "view_reports": self.can_view_reports,
            "manage_users": self.can_manage_users,
            "manage_products": self.can_manage_products,
            "manage_services": self.can_manage_services,
            "manage_real_estate": self.can_manage_real_estate,
            "manage_settings": self.can_manage_settings,
            "manage_whatsapp_chats": self.can_manage_whatsapp_chats,
            "manage_whatsapp_calls": self.can_manage_whatsapp_calls,
        }
        return permission_map.get(permission_name, False)

    def allows_team_activity(self, category_key: str) -> bool:
        """category_key is one of the team_activity_settings_key() outputs."""
        if not self.is_active:
            return False
        category_map = {
            "team_activity_status": self.notify_team_activity_status,
            "team_activity_action": self.notify_team_activity_action,
            "team_activity_overdue": self.notify_team_activity_overdue,
        }
        return category_map.get(category_key, False)


class ImpersonationSession(models.Model):
    """
    Short-lived handoff code for super-admin impersonation into the CRM app.
    Stored in DB so all workers/processes can read it (unlike per-process cache).
    Exchange is one-time with a short post-use grace window for idempotent retries.
    """
    code = models.CharField(max_length=64, unique=True, db_index=True)
    payload = models.JSONField(help_text="Dict: access, refresh, user, impersonation meta")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    impersonator = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="impersonation_sessions_started",
    )
    target_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="impersonation_sessions_as_target",
    )
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="impersonation_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_impersonation_session"
        ordering = ["-created_at"]

    def __str__(self):
        return f"ImpersonationSession {self.code[:12]}..."


class WorkDaySummary(models.Model):
    """
    Accumulated *measured* CRM usage per user per company-local day.

    Written incrementally by the work-session ping (accounts/work_tracking.py): each
    ping credits the elapsed interval since ``User.work_last_ping_at``, bucketed into
    the company-local date at write time. Bucketing on write is what makes a session
    that crosses local midnight split naturally (the 23:59 ping credits day D, the
    00:01 ping credits D+1) and lets the report aggregate with no timezone math.

    Distinct from ``User.work_start_time``/``work_end_time``, which are *schedule*
    config for lead routing, not measurement.
    """

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="work_day_summaries",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="work_days",
    )
    work_date = models.DateField(help_text="Local calendar date in the company's timezone.")
    active_seconds = models.PositiveIntegerField(
        default=0, help_text="Total measured usage seconds (web + mobile)."
    )
    web_seconds = models.PositiveIntegerField(default=0)
    mobile_seconds = models.PositiveIntegerField(default=0)
    first_activity_at = models.DateTimeField(help_text="UTC instant of the first credited ping.")
    last_activity_at = models.DateTimeField(help_text="UTC instant of the most recent ping.")
    ping_count = models.PositiveIntegerField(default=0)
    idle_pause_count = models.PositiveSmallIntegerField(
        default=0, help_text="Times the user resumed after exceeding the idle timeout."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_work_day_summary"
        ordering = ["-work_date"]
        constraints = [
            models.UniqueConstraint(fields=["user", "work_date"], name="uniq_work_day_per_user"),
        ]
        # The unique constraint's index serves the per-user "today" lookup; this one
        # serves the company-wide range scan the Employees Report does.
        # No active_seconds <= 86400 check: a DST fall-back day is legitimately 25h.
        indexes = [
            models.Index(fields=["company", "work_date"], name="workday_company_date_idx"),
        ]

    def __str__(self):
        return f"WorkDaySummary(user={self.user_id}, date={self.work_date}, secs={self.active_seconds})"

class UserDevice(models.Model):
    """
    One registered push target, with the platform it belongs to.

    The user model has carried a bare list of FCM tokens since before there was a
    web client. That was fine while every token was a phone, but it cannot answer
    "deliver this to browsers only" — so a change that concerns an open desktop tab
    would vibrate the same person's phone, at any hour, with no way to tell the two
    apart or to age out a browser token that will never be seen again.

    This table is additive on purpose. ``User.fcm_tokens`` keeps being written and
    is still what an unfiltered push reads, so nothing about existing delivery
    changes; this is consulted only when a caller asks for a specific platform.
    That makes the rollout reversible — dropping this table would cost platform
    targeting and break nothing else.
    """

    class Platform(models.TextChoices):
        WEB = "web", "Web"
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"
        # Everything registered before this table existed. Reachable by an
        # unfiltered push, never by a platform-targeted one.
        UNKNOWN = "unknown", "Unknown"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="devices",
    )
    # Unique across all users, not per user: FCM issues a token to an app install,
    # so if two people sign in on one device the token must move to whoever signed
    # in last rather than delivering one person's notifications to the other.
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(
        max_length=16,
        choices=Platform.choices,
        default=Platform.UNKNOWN,
        db_index=True,
    )
    user_agent = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_user_device"
        ordering = ["-last_seen_at"]
        indexes = [
            models.Index(fields=["user", "platform"], name="userdevice_user_platform_idx"),
        ]

    def __str__(self):
        return f"UserDevice(user={self.user_id}, platform={self.platform})"

    @classmethod
    def register(cls, user, token, platform=None, user_agent=""):
        """
        Upsert a device, reassigning the token if it belonged to someone else.

        Returns the row. Callers should treat failure as non-fatal — the legacy
        token list is written alongside this and is what unfiltered pushes read,
        so a failure here costs platform targeting, not delivery.
        """
        token = (token or "").strip()
        if not token:
            return None
        platform = (platform or "").strip().lower()
        if platform not in cls.Platform.values:
            platform = cls.Platform.UNKNOWN

        device, _created = cls.objects.update_or_create(
            token=token,
            defaults={
                "user": user,
                "platform": platform,
                "user_agent": (user_agent or "")[:200],
            },
        )
        return device
