"""Auth helpers for subscription checkout endpoints."""

from rest_framework import status

from crm_saas_api.responses import error_response


def require_subscription_owner(request, subscription):
    """
    Ensure the authenticated user is the company owner for this subscription.
    Returns None if OK, or an error_response.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return error_response(
            "Authentication required.",
            code="authentication_required",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    owner = getattr(subscription.company, "owner", None)
    if owner is None or user.id != owner.id:
        return error_response(
            "You do not have permission to pay for this subscription.",
            code="forbidden",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return None


def require_subscription_company_member(request, subscription):
    """
    Allow any authenticated user in the subscription's company (or a super admin)
    to read subscription/payment status. Checkout/pay remains owner-only.
    Returns None if OK, or an error_response.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return error_response(
            "Authentication required.",
            code="authentication_required",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    is_super = getattr(user, "is_superuser", False) or (
        callable(getattr(user, "is_super_admin", None)) and user.is_super_admin()
    )
    if is_super:
        return None

    if getattr(user, "company_id", None) == subscription.company_id:
        return None

    return error_response(
        "You do not have permission to view this subscription.",
        code="forbidden",
        status_code=status.HTTP_403_FORBIDDEN,
    )
