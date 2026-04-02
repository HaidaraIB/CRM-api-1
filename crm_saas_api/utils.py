"""
Shared utilities used across multiple Django apps.
Centralises duplicated logic (SMTP connections, code generation, etc.).
"""
from django.core.mail.backends.smtp import EmailBackend


class SMTPNotActiveError(RuntimeError):
    """Raised when SMTP settings are not active."""
    pass


def get_smtp_connection():
    """
    Build and return an SMTP EmailBackend from the platform SMTPSettings singleton.
    Raises SMTPNotActiveError if SMTP is not configured / active.
    """
    from settings.models import SMTPSettings

    smtp_settings = SMTPSettings.get_settings()
    if not smtp_settings.is_active:
        raise SMTPNotActiveError(
            "SMTP is not active. Please configure and enable SMTP settings."
        )

    return EmailBackend(
        host=smtp_settings.host,
        port=smtp_settings.port,
        username=smtp_settings.username,
        password=smtp_settings.password,
        use_tls=smtp_settings.use_tls,
        use_ssl=smtp_settings.use_ssl,
        fail_silently=False,
    )


def generate_sequential_code(model_class, company, prefix, max_attempts=1000):
    """
    Generate a unique sequential code like DEV001, PROJ002, etc.
    Avoids duplicating this pattern across every ViewSet.perform_create.
    """
    last_obj = (
        model_class.objects.filter(company=company, code__startswith=prefix)
        .order_by("-id")
        .first()
    )

    new_num = 1
    if last_obj and last_obj.code:
        try:
            suffix = last_obj.code.replace(prefix, "").strip()
            if suffix:
                new_num = int(suffix) + 1
        except (ValueError, AttributeError):
            new_num = 1

    for _ in range(max_attempts):
        candidate = f"{prefix}{str(new_num).zfill(3)}"
        if not model_class.objects.filter(company=company, code=candidate).exists():
            return candidate
        new_num += 1

    raise ValueError(f"Unable to generate unique {prefix} code after {max_attempts} attempts")
