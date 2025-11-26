import logging
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from settings.models import SMTPSettings

logger = logging.getLogger(__name__)


def _get_smtp_connection():
    smtp_settings = SMTPSettings.get_settings()
    if not smtp_settings.is_active:
        raise RuntimeError(
            "SMTP is not active. Please configure SMTP settings before sending emails."
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


def _get_frontend_base_url():
    base_url = getattr(settings, "FRONTEND_APP_URL", "http://localhost:3000")
    return base_url.rstrip("/")


def send_email_verification(user, verification, language="en"):
    """
    Send email verification message that includes both OTP code and direct link.
    """
    try:
        smtp_settings = SMTPSettings.get_settings()
        if not smtp_settings.is_active:
            logger.warning("SMTP is disabled; skipping verification email.")
            return False

        connection = _get_smtp_connection()
        from_email = (
            f"{smtp_settings.from_name} <{smtp_settings.from_email}>"
            if smtp_settings.from_name
            else smtp_settings.from_email
        )

        subject = _("Verify your LOOP CRM email")
        frontend_base = _get_frontend_base_url()
        verification_link = (
            f"{frontend_base}/verify-email?token={verification.token}&email={user.email}"
        )

        greeting_name = user.first_name or user.username or _("there")
        text_body = _(
            "Hi {name},\n\n"
            "Thanks for signing up to LOOP CRM. Use the verification code below or "
            "click the link to verify your email address.\n\n"
            "Verification code: {code}\n"
            "Verification link: {link}\n\n"
            "This code expires on {expiry}.\n\n"
            "If you pressed \"verify later\" inside the app, you can always return to this email and use the link above.\n\n"
            "If you did not create an account, you can ignore this email."
        ).format(
            name=greeting_name,
            code=verification.code,
            link=verification_link,
            expiry=verification.expires_at.strftime("%Y-%m-%d %H:%M %Z"),
        )

        html_content = render_to_string(
            "accounts/email_verification.html",
            {
                "subject": subject,
                "greeting_name": greeting_name,
                "code": verification.code,
                "verification_link": verification_link,
                "expires_at": verification.expires_at,
                "support_email": smtp_settings.from_email,
                "brand_color": "#6f3ef0",
            },
        )
        plain_body = strip_tags(html_content) or text_body

        email = EmailMultiAlternatives(
            subject=str(subject),
            body=plain_body,
            from_email=from_email,
            to=[user.email],
            connection=connection,
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info("Verification email sent to %s", user.email)
        return True
    except Exception as exc:
        logger.error("Failed to send verification email: %s", exc)
        return False

