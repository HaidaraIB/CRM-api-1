from .email_registration_policy import effective_registration_email_verification_required
from .phone_otp_policy import effective_phone_otp_required
from .two_factor_policy import is_company_owner


def login_verification_error(user):
    """
    Return a structured login error when required verifications are missing.
    Super admins are exempt. Non-owners are exempt.

    For the company owner only, email and/or phone must be verified when the
    matching super-admin Registration OTP toggles are enabled (same cache keys
    as registration). If both toggles are off, login does not enforce those checks.
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

    if email_required and not email_ok and phone_required and not phone_ok:
        return {
            "error": "Email and phone verification are required before login.",
            "code": "email_phone_not_verified",
        }
    if email_required and not email_ok:
        return {
            "error": "Email verification is required before login.",
            "code": "email_not_verified",
        }
    return {
        "error": "Phone verification is required before login.",
        "code": "phone_not_verified",
    }
