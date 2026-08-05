"""Domain-specific API exceptions."""

from rest_framework.exceptions import APIException


class LoginVerificationRequired(APIException):
    """
    Company owner cannot complete JWT login until required verifications pass.
    Handled in crm_saas_api.exception_handler to emit a structured error envelope.
    """

    status_code = 403

    def __init__(
        self,
        *,
        message: str,
        business_code: str,
        verify_email_url: str | None = None,
        verify_phone_url: str | None = None,
    ):
        self.business_code = business_code
        self.verify_email_url = verify_email_url or ""
        self.verify_phone_url = verify_phone_url or ""
        super().__init__(detail=message, code=business_code)


class AccountLocked(APIException):
    """
    Login rejected because the account is temporarily locked after failed attempts.
    Handled in crm_saas_api.exception_handler.
    """

    status_code = 403
    default_code = "ACCOUNT_LOCKED"

    def __init__(
        self,
        *,
        message: str = "Too many failed login attempts. Please try again later.",
        retry_after_seconds: int = 0,
        lockout_until: str | None = None,
    ):
        self.business_code = "ACCOUNT_LOCKED"
        self.retry_after_seconds = max(0, int(retry_after_seconds or 0))
        self.lockout_until = lockout_until or ""
        super().__init__(detail=message, code=self.business_code)
