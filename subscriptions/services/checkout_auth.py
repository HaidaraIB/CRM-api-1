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
