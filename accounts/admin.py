from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Role, EmailVerification, LimitedAdmin, SupervisorPermission, PasswordReset, TwoFactorAuth
from companies.models import Company


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for User model"""

    list_display = [
        "username",
        "email",
        "first_name",
        "last_name",
        "phone",
        "role",
        "company",
        "email_verified",
        "is_active",
        "is_staff",
        "date_joined",
    ]
    list_filter = [
        "role",
        "company",
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined",
    ]
    search_fields = [
        "username",
        "email",
        "first_name",
        "last_name",
        "phone",
    ]
    ordering = ["-date_joined"]

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Additional Information", {"fields": ("role", "company", "phone", "email_verified")}),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Additional Information", {"fields": ("role", "company", "email", "phone")}),
    )

    def save_model(self, request, obj, form, change):
        """Override save to automatically update company owner if user is admin"""
        old_company = None
        old_role = None
        if change and obj.pk:
            try:
                old_user = User.objects.select_related('company').get(pk=obj.pk)
                old_company = old_user.company
                old_role = old_user.role
            except User.DoesNotExist:
                pass
        
        # حفظ User أولاً
        super().save_model(request, obj, form, change)
        
        # إعادة تحميل من قاعدة البيانات للحصول على القيم المحدثة
        if obj.pk:
            updated_user = User.objects.select_related('company').get(pk=obj.pk)
            new_company = updated_user.company
            new_role = updated_user.role
            user_obj = updated_user
        else:
            new_company = obj.company
            new_role = obj.role
            user_obj = obj
        
        # إذا كان المستخدم admin وله company، ربط Company.owner به تلقائياً
        if new_role == Role.ADMIN.value and new_company:
            # ربط Company.owner بالمستخدم (إذا لم يكن مرتبطاً بالفعل)
            if new_company.owner != user_obj:
                new_company.owner = user_obj
                new_company.save(update_fields=['owner'])
        
        # إذا تغيرت company أو role، تحديث owner في company القديمة
        if old_company and old_company != new_company and old_company.owner == user_obj:
            old_company.owner = None
            old_company.save(update_fields=['owner'])
        
        # إذا لم يعد المستخدم admin أو تمت إزالة company، إزالة owner من company
        if old_company and old_company == new_company:
            if (new_role != Role.ADMIN.value or not new_company) and old_company.owner == user_obj:
                old_company.owner = None
                old_company.save(update_fields=['owner'])


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ["user", "code", "token", "is_verified", "expires_at", "created_at"]
    search_fields = ["user__email", "user__username", "code", "token"]
    list_filter = ["is_verified", "expires_at", "created_at"]


@admin.register(PasswordReset)
class PasswordResetAdmin(admin.ModelAdmin):
    list_display = ["user", "code", "token", "is_used", "expires_at", "used_at", "created_at"]
    search_fields = ["user__email", "user__username", "code", "token"]
    list_filter = ["is_used", "expires_at", "created_at"]
    readonly_fields = ["created_at", "used_at"]


@admin.register(TwoFactorAuth)
class TwoFactorAuthAdmin(admin.ModelAdmin):
    list_display = ["user", "code", "token", "is_verified", "expires_at", "verified_at", "created_at"]
    search_fields = ["user__email", "user__username", "code", "token"]
    list_filter = ["is_verified", "expires_at", "created_at"]
    readonly_fields = ["created_at", "verified_at"]


@admin.register(LimitedAdmin)
class LimitedAdminAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "is_active",
        "can_view_dashboard",
        "can_manage_tenants",
        "can_manage_subscriptions",
        "created_by",
        "created_at",
    ]
    list_filter = [
        "is_active",
        "can_view_dashboard",
        "can_manage_tenants",
        "can_manage_subscriptions",
        "can_manage_payment_gateways",
        "can_view_reports",
        "can_manage_communication",
        "can_manage_settings",
        "created_at",
    ]
    search_fields = ["user__username", "user__email", "user__first_name", "user__last_name"]
    ordering = ["-created_at"]
    
    fieldsets = (
        ("User", {"fields": ("user", "created_by")}),
        ("Status", {"fields": ("is_active",)}),
        ("Permissions", {
            "fields": (
                "can_view_dashboard",
                "can_manage_tenants",
                "can_manage_subscriptions",
                "can_manage_payment_gateways",
                "can_view_reports",
                "can_manage_communication",
                "can_manage_settings",
                "can_manage_limited_admins",
            )
        }),
    )
    
    readonly_fields = ["created_at", "updated_at"]


@admin.register(SupervisorPermission)
class SupervisorPermissionAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "is_active",
        "can_manage_leads",
        "can_manage_deals",
        "can_manage_tasks",
        "can_manage_users",
        "created_at",
    ]
    list_filter = ["is_active", "can_manage_leads", "can_manage_deals", "can_manage_users", "created_at"]
    search_fields = ["user__username", "user__email", "user__first_name", "user__last_name"]
    ordering = ["-created_at"]
    fieldsets = (
        ("User", {"fields": ("user",)}),
        ("Status", {"fields": ("is_active",)}),
        ("Permissions", {
            "fields": (
                "can_manage_leads",
                "can_manage_deals",
                "can_manage_tasks",
                "can_view_reports",
                "can_manage_users",
                "can_manage_products",
                "can_manage_services",
                "can_manage_real_estate",
                "can_manage_settings",
            )
        }),
    )
    readonly_fields = ["created_at", "updated_at"]
