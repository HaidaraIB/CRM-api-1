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
