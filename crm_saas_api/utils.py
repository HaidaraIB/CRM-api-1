"""
Shared utilities used across multiple Django apps.
Centralises duplicated logic (outbound email connections, code generation, etc.).
"""
from django.conf import settings as django_settings

from crm_saas_api.email_exceptions import OutboundEmailNotConfiguredError, SMTPNotActiveError
from crm_saas_api.resend_email_backend import ResendEmailBackend


def get_platform_email_display_name(smtp_settings):
    """
    Display name for the From header and templates.
    Uses SMTPSettings.from_name when set; otherwise PLATFORM_EMAIL_SENDER_DISPLAY_NAME.
    """
    custom = (smtp_settings.from_name or "").strip()
    if custom:
        return custom
    fallback = getattr(
        django_settings, "PLATFORM_EMAIL_SENDER_DISPLAY_NAME", "LOOP CRM"
    ) or "LOOP CRM"
    return str(fallback).strip() or "LOOP CRM"


def format_platform_from_address(smtp_settings):
    """RFC 5322 From line: ``Display Name <addr@domain>`` for Resend / Django mail."""
    name = get_platform_email_display_name(smtp_settings)
    return f"{name} <{smtp_settings.from_email}>"


def get_smtp_connection():
    """
    Build and return a Resend-backed email backend from platform settings.

    Uses ``SMTPSettings`` for the enable switch and from-address branding; the
    Resend API key must be set as ``RESEND_API_KEY`` in the environment / Django settings.

    Raises OutboundEmailNotConfiguredError (alias ``SMTPNotActiveError``) when email
    is disabled or Resend is not configured.
    """
    from settings.models import SMTPSettings

    smtp_settings = SMTPSettings.get_settings()
    if not smtp_settings.is_active:
        raise OutboundEmailNotConfiguredError(
            "Outbound email is disabled. Enable it in platform email settings and set RESEND_API_KEY."
        )

    api_key = (getattr(django_settings, "RESEND_API_KEY", None) or "").strip()
    if not api_key:
        raise OutboundEmailNotConfiguredError(
            "RESEND_API_KEY is not set. Add it to the environment to send email via Resend."
        )

    return ResendEmailBackend(api_key=api_key, fail_silently=False)


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
