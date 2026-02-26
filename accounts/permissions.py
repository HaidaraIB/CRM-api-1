from rest_framework import permissions
from django.utils import timezone
from subscriptions.models import Subscription


class HasActiveSubscription(permissions.BasePermission):
    """
    Permission class to check if user's company has an active subscription.
    Checks both is_active flag and end_date to ensure subscription is truly active.
    Super Admin is exempt from this check.
    """
    message = "Your subscription is not active or has expired. Please contact support or complete your payment to access the system."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super Admin doesn't need active subscription
        if request.user.is_super_admin():
            return True
        
        # Check if user has a company
        if not request.user.company:
            return False
        
        # Check if company has an active subscription
        # Must be: is_active=True AND end_date > now
        now = timezone.now()
        active_subscription = Subscription.objects.filter(
            company=request.user.company,
            is_active=True,
            end_date__gt=now
        ).first()
        
        # If subscription exists but end_date has passed, update is_active to False
        if not active_subscription:
            # Check if there's a subscription that should be deactivated
            expired_subscription = Subscription.objects.filter(
                company=request.user.company,
                is_active=True,
                end_date__lte=now
            ).first()
            
            if expired_subscription:
                expired_subscription.is_active = False
                expired_subscription.save(update_fields=['is_active', 'updated_at'])
        
        return active_subscription is not None


class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.is_super_admin()
        )


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user and request.user.is_authenticated and request.user.is_admin()
        )


class CanManageSupervisors(permissions.BasePermission):
    """Only company admin can manage supervisors (grant/revoke permissions)."""
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.is_admin()
            and request.user.company is not None
        )


class IsAdminOrReadOnlyForEmployee(permissions.BasePermission):
    """
    Permission class that allows:
    - Admin: All operations (GET, POST, PUT, DELETE)
    - Employee: Only GET (read-only) operations
    - Supervisor: only if subclass sets supervisor_permission_name and they have it
    """
    supervisor_permission_name = None  # e.g. 'manage_settings'; if set, supervisor with that permission gets full access

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Admin can do everything
        if request.user.is_admin():
            return True

        # Supervisor with the required permission can do everything
        perm = getattr(self, 'supervisor_permission_name', None)
        if perm and request.user.supervisor_has_permission(perm):
            return True

        # Employee can only do GET requests
        if request.user.is_employee():
            return request.method in permissions.SAFE_METHODS

        return False


class IsAdminOrSupervisorLeadsOrReadOnlyForEmployee(IsAdminOrReadOnlyForEmployee):
    """Admin or supervisor with can_manage_leads: full access; employee: read-only."""
    supervisor_permission_name = "manage_leads"


class IsAdminOrSupervisorSettingsOrReadOnlyForEmployee(IsAdminOrReadOnlyForEmployee):
    """Admin or supervisor with can_manage_settings: full access; employee: read-only."""
    supervisor_permission_name = "manage_settings"


class IsAdminOrSupervisorSettingsOrLeadsReadOnlyForEmployee(permissions.BasePermission):
    """
    Logical permission for lead-related settings (channels, stages, statuses, call-methods).
    - Admin: full access.
    - Supervisor with can_manage_settings: full access.
    - Supervisor with can_manage_leads only: read (GET) so Leads/Activities pages can load.
    - Employee: read-only.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_admin():
            return True
        if request.user.is_supervisor():
            if request.user.supervisor_has_permission("manage_settings"):
                return True
            if request.user.supervisor_has_permission("manage_leads") and request.method in permissions.SAFE_METHODS:
                return True
            return False
        if request.user.is_employee():
            return request.method in permissions.SAFE_METHODS
        return False


class IsEmployee(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.is_employee()
        )


class IsSuperAdminOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and (request.user.is_super_admin() or request.user.is_admin())
        )


class IsSuperAdminOrAdminOrEmployee(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


class CanAccessClient(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        # Handle ClientTask which has client instead of company
        if hasattr(obj, "client") and not hasattr(obj, "company"):
            obj = obj.client

        # Check company access
        if hasattr(obj, "company"):
            if not request.user.can_access_company_data(obj.company):
                return False

        if request.user.is_employee():
            if hasattr(obj, "assigned_to"):
                return obj.assigned_to == request.user
            return False

        if request.user.is_admin():
            return obj.company == request.user.company

        # Supervisor with Leads permission can access (Activities/Leads section uses client-tasks)
        if request.user.supervisor_has_permission("manage_leads"):
            return getattr(obj, "company", None) == request.user.company

        return False


class CanAccessDeal(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.user.is_employee():
            if hasattr(obj, "employee"):
                return obj.employee == request.user
            return False

        if request.user.is_admin():
            return obj.company == request.user.company

        if request.user.supervisor_has_permission("manage_deals"):
            return obj.company == request.user.company

        return False


class CanAccessTask(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.user.is_employee():
            if hasattr(obj, "deal") and hasattr(obj.deal, "employee"):
                return obj.deal.employee == request.user
            return False

        if request.user.is_admin():
            return obj.deal.company == request.user.company

        if request.user.supervisor_has_permission("manage_tasks"):
            return obj.deal.company == request.user.company

        return False


class CanAccessUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return request.user and request.user.can_access_user(obj)


class CanAccessCompanyData(permissions.BasePermission):
    """
    Generic permission class for objects that have a company field.
    Super Admin can access all, Admin can access their company's data.
    Subclasses can set supervisor_permission_name to allow supervisors with that permission.
    """

    supervisor_permission_name = None  # e.g. 'manage_products'

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if not request.user:
            return False
        if request.user.can_access_company_data(obj.company):
            return True
        if self.supervisor_permission_name and request.user.supervisor_has_permission(self.supervisor_permission_name):
            return obj.company == request.user.company
        return False


class CanAccessDeveloper(CanAccessCompanyData):
    supervisor_permission_name = "manage_real_estate"


class CanAccessProject(CanAccessCompanyData):
    supervisor_permission_name = "manage_real_estate"


class CanAccessUnit(CanAccessCompanyData):
    supervisor_permission_name = "manage_real_estate"


class CanAccessOwner(CanAccessCompanyData):
    supervisor_permission_name = "manage_real_estate"


class CanAccessProductCategory(CanAccessCompanyData):
    supervisor_permission_name = "manage_products"


class CanAccessProduct(CanAccessCompanyData):
    supervisor_permission_name = "manage_products"


class CanAccessSupplier(CanAccessCompanyData):
    supervisor_permission_name = "manage_products"


class CanAccessServiceProvider(CanAccessCompanyData):
    supervisor_permission_name = "manage_services"


class CanAccessService(CanAccessCompanyData):
    supervisor_permission_name = "manage_services"


class CanAccessServicePackage(CanAccessCompanyData):
    supervisor_permission_name = "manage_services"


# Limited Admin Permissions for Super Admin Panel
class IsSuperAdminOrLimitedAdmin(permissions.BasePermission):
    """
    Base permission class that allows Super Admin or active Limited Admin.
    Limited Admin must be active to access.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super Admin always has access
        if request.user.is_super_admin():
            return True
        
        # Check if user is an active Limited Admin
        try:
            limited_admin = request.user.limited_admin_profile
            return limited_admin.is_active
        except:
            return False


class CanViewDashboard(IsSuperAdminOrLimitedAdmin):
    """Permission to view dashboard - requires can_view_dashboard permission"""
    message = "You do not have permission to view the dashboard."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        # Super Admin always has access
        if request.user.is_super_admin():
            return True
        
        # Check Limited Admin permission
        try:
            limited_admin = request.user.limited_admin_profile
            return limited_admin.is_active and limited_admin.can_view_dashboard
        except:
            return False


class CanManageTenants(IsSuperAdminOrLimitedAdmin):
    """Permission to manage tenants (companies). Read (GET) allowed with can_view_dashboard; write requires can_manage_tenants."""
    message = "You do not have permission to manage tenants."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        # Super Admin always has access
        if request.user.is_super_admin():
            return True
        
        try:
            limited_admin = request.user.limited_admin_profile
            if not limited_admin.is_active:
                return False
            # Read-only (dashboard): allow if can_view_dashboard or can_manage_tenants
            if request.method in permissions.SAFE_METHODS:
                return limited_admin.can_view_dashboard or limited_admin.can_manage_tenants
            return limited_admin.can_manage_tenants
        except:
            return False


class CanManageSubscriptions(IsSuperAdminOrLimitedAdmin):
    """Permission to manage subscriptions. Read (GET) allowed with can_view_dashboard; write requires can_manage_subscriptions."""
    message = "You do not have permission to manage subscriptions."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        if request.user.is_super_admin():
            return True
        
        try:
            limited_admin = request.user.limited_admin_profile
            if not limited_admin.is_active:
                return False
            if request.method in permissions.SAFE_METHODS:
                return limited_admin.can_view_dashboard or limited_admin.can_manage_subscriptions
            return limited_admin.can_manage_subscriptions
        except:
            return False


class CanManagePlans(IsSuperAdminOrLimitedAdmin):
    """Permission to manage plans. Read (GET) allowed with can_view_dashboard; write requires can_manage_subscriptions."""
    message = "You do not have permission to manage plans."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        if request.user.is_super_admin():
            return True
        
        try:
            limited_admin = request.user.limited_admin_profile
            if not limited_admin.is_active:
                return False
            if request.method in permissions.SAFE_METHODS:
                return limited_admin.can_view_dashboard or limited_admin.can_manage_subscriptions
            return limited_admin.can_manage_subscriptions
        except:
            return False


class CanManagePayments(IsSuperAdminOrLimitedAdmin):
    """Permission to manage payments. Read (GET) allowed with can_view_dashboard; write requires can_manage_payment_gateways or can_view_reports."""
    message = "You do not have permission to manage payments."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        if request.user.is_super_admin():
            return True
        
        try:
            limited_admin = request.user.limited_admin_profile
            if not limited_admin.is_active:
                return False
            if request.method in permissions.SAFE_METHODS:
                return (
                    limited_admin.can_view_dashboard or
                    limited_admin.can_manage_payment_gateways or
                    limited_admin.can_view_reports
                )
            return (
                limited_admin.can_manage_payment_gateways or
                limited_admin.can_view_reports
            )
        except:
            return False


class CanManagePaymentGateways(IsSuperAdminOrLimitedAdmin):
    """Permission to manage payment gateways - requires can_manage_payment_gateways permission"""
    message = "You do not have permission to manage payment gateways."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        # Super Admin always has access
        if request.user.is_super_admin():
            return True
        
        # Check Limited Admin permission
        try:
            limited_admin = request.user.limited_admin_profile
            return limited_admin.is_active and limited_admin.can_manage_payment_gateways
        except:
            return False


class CanViewReports(IsSuperAdminOrLimitedAdmin):
    """Permission to view reports - requires can_view_reports permission"""
    message = "You do not have permission to view reports."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        # Super Admin always has access
        if request.user.is_super_admin():
            return True
        
        # Check Limited Admin permission
        try:
            limited_admin = request.user.limited_admin_profile
            return limited_admin.is_active and limited_admin.can_view_reports
        except:
            return False


class CanManageCommunication(IsSuperAdminOrLimitedAdmin):
    """Permission to manage communication - requires can_manage_communication permission"""
    message = "You do not have permission to manage communication."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        # Super Admin always has access
        if request.user.is_super_admin():
            return True
        
        # Check Limited Admin permission
        try:
            limited_admin = request.user.limited_admin_profile
            return limited_admin.is_active and limited_admin.can_manage_communication
        except:
            return False


class CanManageSettings(IsSuperAdminOrLimitedAdmin):
    """Permission to manage settings. Read (GET, e.g. audit logs) allowed with can_view_dashboard; write requires can_manage_settings."""
    message = "You do not have permission to manage settings."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        if request.user.is_super_admin():
            return True
        
        try:
            limited_admin = request.user.limited_admin_profile
            if not limited_admin.is_active:
                return False
            if request.method in permissions.SAFE_METHODS:
                return limited_admin.can_view_dashboard or limited_admin.can_manage_settings
            return limited_admin.can_manage_settings
        except:
            return False


class CanManageLimitedAdmins(IsSuperAdminOrLimitedAdmin):
    """Permission to manage limited admins - requires can_manage_limited_admins permission"""
    message = "You do not have permission to manage limited admins."
    
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        
        # Super Admin always has access
        if request.user.is_super_admin():
            return True
        
        # Check Limited Admin permission
        try:
            limited_admin = request.user.limited_admin_profile
            return limited_admin.is_active and limited_admin.can_manage_limited_admins
        except:
            return False
