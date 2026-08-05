from rest_framework import permissions


class IsCompanyLibraryUser(permissions.BasePermission):
    """Any authenticated user with a company may browse/download."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "company_id", None)
        )


class IsCompanyLibraryAdmin(permissions.BasePermission):
    """Company admin (Owner) may upload/rename/delete."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "company_id", None)
            and user.is_admin()
        )
