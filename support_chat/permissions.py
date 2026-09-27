from rest_framework.permissions import BasePermission

from accounts.permissions import IsSuperAdmin
from accounts.two_factor_policy import is_company_owner


class IsCompanyOwner(BasePermission):
    """Company owner only; does not require an active subscription."""

    message = "Only the company owner can access support chat."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return is_company_owner(user)


class IsSupportAgent(IsSuperAdmin):
    """Platform super admin (shared support inbox)."""

    message = "Super admin access required."
