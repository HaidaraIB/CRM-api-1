import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from crm_saas_api.utils import format_platform_from_address
from settings.models import SMTPSettings

logger = logging.getLogger(__name__)


def send_registration_otp_email(email: str, code: str, expire_minutes: int = 10) -> bool:
    try:
        smtp_settings = SMTPSettings.get_settings()
        if not smtp_settings.is_active:
            logger.warning("Outbound email is disabled; skipping registration email OTP.")
            return False

        from crm_saas_api.utils import get_smtp_connection

        connection = get_smtp_connection()
        from_email = format_platform_from_address(smtp_settings)
        expiry_at = timezone.now() + timedelta(minutes=expire_minutes)

        subject = "Your registration verification code - LOOP CRM"
        text_body = (
            f"Your verification code is: {code}\n\n"
            f"This code expires at {expiry_at.strftime('%Y-%m-%d %H:%M %Z')}.\n"
            f"If you did not request this, you can ignore this email."
        )
        html_body = (
            "<p>Your verification code is:</p>"
            f"<h2>{code}</h2>"
            f"<p>This code expires in about {expire_minutes} minutes.</p>"
            "<p>If you did not request this, you can ignore this email.</p>"
        )

        email_obj = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[email],
            connection=connection,
        )
        email_obj.attach_alternative(html_body, "text/html")
        email_obj.send()
        return True
    except Exception as exc:
        logger.error("Failed to send registration OTP email: %s", exc)
        return False
