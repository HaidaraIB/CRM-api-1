"""Errors for platform outbound email (Resend)."""


class OutboundEmailNotConfiguredError(RuntimeError):
    """Raised when outbound email is disabled or Resend is not configured."""

    pass


# Backward-compatible alias
SMTPNotActiveError = OutboundEmailNotConfiguredError
