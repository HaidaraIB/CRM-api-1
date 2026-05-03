from urllib.parse import quote

from .email_registration_policy import effective_registration_email_verification_required
from .phone_otp_policy import effective_phone_otp_required
from .two_factor_policy import is_company_owner


def _verify_email_path(owner_email: str) -> str:
    safe = quote((owner_email or "").strip(), safe="")
    return f"/verify-email?email={safe}" if safe else "/verify-email"


def login_verification_failure(user):
    """
    If the company owner must complete verification before login, return a dict suitable
    for error_response / LoginVerificationRequired. Otherwise None.

    Clients should localize using ``code``; ``verify_*_url`` are in-app paths for web.
    """
    if not user or user.is_super_admin():
        return None

    if not is_company_owner(user):
        return None

    email_required = effective_registration_email_verification_required()
    phone_required = effective_phone_otp_required()

    if not email_required and not phone_required:
        return None

    email_ok = not email_required or bool(getattr(user, "email_verified", False))
    phone_ok = not phone_required or bool(getattr(user, "phone_verified", False))

    if email_ok and phone_ok:
        return None

    owner_email = getattr(user, "email", "") or ""
    email_path = _verify_email_path(owner_email) if email_required and not email_ok else None
    phone_path = "/verify-phone" if phone_required and not phone_ok else None

    if email_required and not email_ok and phone_required and not phone_ok:
        return {
            "code": "email_phone_not_verified",
            "message": "Email and phone verification required.",
            "verify_email_url": email_path,
            "verify_phone_url": phone_path,
        }

    if email_required and not email_ok:
        return {
            "code": "email_not_verified",
            "message": "Email verification required.",
            "verify_email_url": email_path,
            "verify_phone_url": None,
        }

    return {
        "code": "phone_not_verified",
        "message": "Phone verification required.",
        "verify_email_url": None,
        "verify_phone_url": phone_path,
    }
