from rest_framework import permissions
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from subscriptions.models import Subscription


def is_impersonating(request):
    """
    True when the Bearer access token carries the impersonation claim
    (super-admin support session acting as a company owner).
    """
    token = getattr(request, "auth", None)
    if token is None:
        return False
    try:
        return bool(token.get("impersonation"))
    except Exception:
        return False


class HasActiveSubscription(permissions.BasePermission):
    """
    Permission class to check if user's company has an active subscription.
    Checks both is_active flag and end_date to ensure subscription is truly active.
    Super Admin and active impersonation sessions are exempt from this check.
    """
    message = "Your subscription is not active or has expired. Please contact support or Complete Your Payment to access the system."
    
    CACHE_TTL = 300  # 5 minutes

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_super_admin():
            return True

        # Support must inspect expired / deactivated tenants while impersonating.
        if is_impersonating(request):
            return True

        if not request.user.company:
            return False

        company_id = request.user.company_id
        cache_key = f"active_sub_{company_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        now = timezone.now()
        active_subscription = Subscription.objects.filter(
            company_id=company_id,
            is_active=True,
            end_date__gt=now,
        ).only("id").first()

        if not active_subscription:
            Subscription.objects.filter(
                company_id=company_id,
                is_active=True,
                end_date__lte=now,
            ).update(is_active=False)

        result = active_subscription is not None
        cache.set(cache_key, result, self.CACHE_TTL)
        return result


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

        # Data entry: read-only on views using this class (same as employee)
        if request.user.is_data_entry():
            return request.method in permissions.SAFE_METHODS

        # Employee / doctor / reception: read-only (GET/HEAD/OPTIONS)
        if request.user.is_employee() or request.user.is_doctor() or request.user.is_reception():
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
        if request.user.is_data_entry():
            return request.method in permissions.SAFE_METHODS
        if request.user.is_employee() or request.user.is_doctor() or request.user.is_reception():
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

        if request.user.is_data_entry():
            return False

        if request.user.is_reception():
            if request.method == "DELETE":
                return False
            return getattr(obj, "company", None) == request.user.company

        if request.user.is_employee() or request.user.is_doctor():
            if hasattr(obj, "assigned_to"):
                return obj.assigned_to == request.user
            return False

        if request.user.is_admin():
            return obj.company == request.user.company

        # Supervisor with Leads permission can access (Activities/Leads section uses client-tasks)
        if request.user.supervisor_has_permission("manage_leads"):
            return getattr(obj, "company", None) == request.user.company

        return False


class DenyDataEntryNonLeadAPI(permissions.BasePermission):
    """
    Data-entry users may only use client (lead) list/create and related settings.
    Deny deals, tasks, and client activity APIs at the view level.
    """

    message = "This action is not available for data entry users."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return True
        return not user.is_data_entry()


class CanAccessDeal(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.user.is_employee() or request.user.is_doctor():
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
        if request.user.is_employee() or request.user.is_doctor():
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
class LimitedAdminPermission(permissions.BasePermission):
    """
    Configurable base permission for Limited Admins.
    Allows Super Admin by default.
    """
    required_permission = None    # Simple: single permission for all methods
    write_permission = None       # Read/write split: needed for write
    read_permissions = []         # Read/write split: any of these + write_permission allow GET

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
            
        if request.user.is_super_admin():
            return True
            
        try:
            la = request.user.limited_admin_profile
            if not la.is_active:
                return False
        except ObjectDoesNotExist:
            return False
            
        # Simple mode
        if self.required_permission:
            return getattr(la, self.required_permission, False)
            
        # Read/write split mode
        if request.method in permissions.SAFE_METHODS:
            allowed = [self.write_permission] + list(self.read_permissions)
            return any(getattr(la, p, False) for p in allowed if p)
            
        if self.write_permission:
            return getattr(la, self.write_permission, False)
            
        return False


class IsSuperAdminOrLimitedAdmin(LimitedAdminPermission):
    """Base permission class that allows Super Admin or active Limited Admin."""
    def has_permission(self, request, view):
        if not super(LimitedAdminPermission, self).has_permission(request, view):
            return False
        if request.user.is_super_admin():
            return True
        try:
            return request.user.limited_admin_profile.is_active
        except ObjectDoesNotExist:
            return False


class CanViewDashboard(LimitedAdminPermission):
    message = "You do not have permission to view the dashboard."
    required_permission = "can_view_dashboard"


class CanManageTenants(LimitedAdminPermission):
    message = "You do not have permission to manage tenants."
    write_permission = "can_manage_tenants"
    read_permissions = ["can_view_dashboard"]


class CanManageSubscriptions(LimitedAdminPermission):
    message = "You do not have permission to manage subscriptions."
    write_permission = "can_manage_subscriptions"
    read_permissions = ["can_view_dashboard"]


class CanManagePlans(LimitedAdminPermission):
    message = "You do not have permission to manage plans."
    write_permission = "can_manage_subscriptions"
    read_permissions = ["can_view_dashboard"]


class CanManagePayments(LimitedAdminPermission):
    message = "You do not have permission to manage payments."
    
    def has_permission(self, request, view):
        if not super(LimitedAdminPermission, self).has_permission(request, view):
            return False
        if request.user.is_super_admin():
            return True
        try:
            la = request.user.limited_admin_profile
            if not la.is_active:
                return False
        except ObjectDoesNotExist:
            return False
            
        if request.method in permissions.SAFE_METHODS:
            return la.can_view_dashboard or la.can_manage_payment_gateways or la.can_view_reports
        return la.can_manage_payment_gateways or la.can_view_reports


class CanViewCompanyInvoicesOrManagePayments(permissions.BasePermission):
    """
    Platform payment managers (admin panel) OR tenant company admin for their own invoices.
    Safe methods + PDF download only — tenants cannot email on behalf of platform.
    """

    message = "You do not have permission to view invoices."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if CanManagePayments().has_permission(request, view):
            return True
        # Tenant company admin/owner: list/retrieve/pdf only
        action = getattr(view, "action", None)
        if action in ("list", "retrieve", "pdf", None) and request.method in permissions.SAFE_METHODS:
            return bool(request.user.is_admin() and request.user.company_id)
        if action == "pdf" and request.method in permissions.SAFE_METHODS:
            return bool(request.user.is_admin() and request.user.company_id)
        return False

    def has_object_permission(self, request, view, obj):
        if CanManagePayments().has_permission(request, view):
            return True
        company_id = getattr(request.user, "company_id", None)
        sub = getattr(obj, "subscription", None)
        return bool(company_id and sub and sub.company_id == company_id)


class CanManagePaymentGateways(LimitedAdminPermission):
    message = "You do not have permission to manage payment gateways."
    required_permission = "can_manage_payment_gateways"


class CanViewReports(LimitedAdminPermission):
    message = "You do not have permission to view reports."
    required_permission = "can_view_reports"


class CanViewCompanyReports(permissions.BasePermission):
    """Tenant CRM reports: company admin/owner or supervisor with view_reports."""

    message = "You do not have permission to view reports."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_admin():
            return True
        if user.is_supervisor() and user.supervisor_has_permission("view_reports"):
            return True
        return False


class CanManageCommunication(LimitedAdminPermission):
    message = "You do not have permission to manage communication."
    required_permission = "can_manage_communication"


class CanManageContent(LimitedAdminPermission):
    message = "You do not have permission to manage platform content."
    required_permission = "can_manage_content"


class CanManageSettings(LimitedAdminPermission):
    message = "You do not have permission to manage settings."
    write_permission = "can_manage_settings"
    read_permissions = ["can_view_dashboard"]


class CanManageLimitedAdmins(LimitedAdminPermission):
    message = "You do not have permission to manage limited admins."
    required_permission = "can_manage_limited_admins"

