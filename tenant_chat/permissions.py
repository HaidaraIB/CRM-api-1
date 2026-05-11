from rest_framework import permissions


class IsTenantChatUser(permissions.BasePermission):
    """Tenant-scoped CRM user (not platform super_admin)."""

    message = "Chat is only available for company users."

    def has_permission(self, request, view):
        u = request.user
        if not u.is_authenticated:
            return False
        if u.is_super_admin():
            return False
        return bool(u.company_id)
