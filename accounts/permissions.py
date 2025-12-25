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


class IsAdminOrReadOnlyForEmployee(permissions.BasePermission):
    """
    Permission class that allows:
    - Admin: All operations (GET, POST, PUT, DELETE)
    - Employee: Only GET (read-only) operations
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Admin can do everything
        if request.user.is_admin():
            return True
        
        # Employee can only do GET requests
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
    """

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return request.user and request.user.can_access_company_data(obj.company)


class CanAccessDeveloper(CanAccessCompanyData):
    """Permission for Developer objects"""

    pass


class CanAccessProject(CanAccessCompanyData):
    """Permission for Project objects"""

    pass


class CanAccessUnit(CanAccessCompanyData):
    """Permission for Unit objects"""

    pass


class CanAccessOwner(CanAccessCompanyData):
    """Permission for Owner objects"""

    pass


class CanAccessProductCategory(CanAccessCompanyData):
    """Permission for ProductCategory objects"""

    pass


class CanAccessProduct(CanAccessCompanyData):
    """Permission for Product objects"""

    pass


class CanAccessSupplier(CanAccessCompanyData):
    """Permission for Supplier objects"""

    pass


class CanAccessServiceProvider(CanAccessCompanyData):
    """Permission for ServiceProvider objects"""

    pass


class CanAccessService(CanAccessCompanyData):
    """Permission for Service objects"""

    pass


class CanAccessServicePackage(CanAccessCompanyData):
    """Permission for ServicePackage objects"""

    pass
