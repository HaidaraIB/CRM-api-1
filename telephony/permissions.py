from __future__ import annotations

from rest_framework import permissions

from integrations.policy import is_integration_allowed

from .models import PbxNode


class HasTelephonyFeature(permissions.BasePermission):
    """
    The company has normal plan access, or an explicit temporary pilot grant,
    and the platform telephony policy allows it.
    """

    message = "Telephony is not available for your company."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not getattr(user, "company_id", None):
            return False
        return is_integration_allowed(user.company, "telephony")


class CanManageTelephonySettings(permissions.BasePermission):
    """
    PBX configuration (extensions, trunks, numbers, routes) is admin-only, read
    included: trunk hosts and routing are not for every employee. Supervisors with
    ``manage_settings`` count as admins here, as elsewhere in Settings.
    """

    message = "Only company admins can manage the phone system."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return bool(user.is_admin() or user.supervisor_has_permission("manage_settings"))


class IsPbxNode(permissions.BasePermission):
    """Request authenticated by a node token (``NodeTokenAuthentication``)."""

    def has_permission(self, request, view):
        return isinstance(getattr(request, "auth", None), PbxNode)
