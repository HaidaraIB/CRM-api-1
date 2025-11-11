from rest_framework import permissions


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