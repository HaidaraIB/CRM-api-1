def login_verification_error(user):
    """
    Return a structured login error when required verifications are missing.
    Super admins are exempt.
    """
    if not user or user.is_super_admin():
        return None

    email_ok = bool(getattr(user, "email_verified", False))
    phone_ok = bool(getattr(user, "phone_verified", False))

    if email_ok and phone_ok:
        return None

    if not email_ok and not phone_ok:
        return {
            "error": "Email and phone verification are required before login.",
            "code": "email_phone_not_verified",
        }
    if not email_ok:
        return {
            "error": "Email verification is required before login.",
            "code": "email_not_verified",
        }
    return {
        "error": "Phone verification is required before login.",
        "code": "phone_not_verified",
    }
