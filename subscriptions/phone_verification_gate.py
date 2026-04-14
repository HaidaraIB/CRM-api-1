from rest_framework import status

from crm_saas_api.responses import error_response


def require_owner_phone_verified(subscription):
    """
    Block payment session creation until the company owner's phone was verified at registration.
    Returns None if OK, or a DRF-style error_response.
    """
    owner = subscription.company.owner
    if not getattr(owner, "phone_verified", False):
        return error_response(
            "Phone verification is required before payment.",
            code="phone_verification_required",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return None
