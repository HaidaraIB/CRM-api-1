"""
Utility functions for sending emails via SMTP
"""

from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend
from django.template.loader import render_to_string
from settings.models import SMTPSettings
from companies.models import Company
from accounts.models import User, Role
from subscriptions.models import Subscription
import logging

logger = logging.getLogger(__name__)


def get_smtp_connection():
    """
    Get SMTP connection using SMTPSettings
    """
    smtp_settings = SMTPSettings.get_settings()

    if not smtp_settings.is_active:
        raise Exception(
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


def get_recipient_emails(broadcast_target):
    """
    Get list of recipient email addresses based on broadcast target

    Args:
        broadcast_target: Can be "all" or a plan ID (e.g., "plan_1", "plan_2")
    """
    emails = []
    companies = Company.objects.none()  # Empty queryset by default

    if broadcast_target == "all":
        # Get all companies with active subscriptions
        companies = Company.objects.all()
    elif broadcast_target.startswith("plan_"):
        # Extract plan ID from target (e.g., "plan_1" -> 1)
        try:
            plan_id = int(broadcast_target.replace("plan_", ""))

            # Get all companies subscribed to this plan
            subscriptions = Subscription.objects.filter(plan_id=plan_id, is_active=True)
            company_ids = subscriptions.values_list("company_id", flat=True)
            companies = Company.objects.filter(id__in=company_ids)
        except (ValueError, TypeError):
            # Invalid plan ID, return empty list
            return []

    for company in companies:
        admin_users = User.objects.filter(company=company, role=Role.ADMIN.value)
        for user in admin_users:
            if user.email:
                emails.append(user.email)

    return list(set(emails))  # Remove duplicates


def send_broadcast_email(broadcast, language="ar"):
    """
    Send broadcast email to recipients based on target

    Args:
        broadcast: Broadcast instance
        language: Language code ('ar' for Arabic, 'en' for English). Default: 'ar'
    """
    try:
        smtp_settings = SMTPSettings.get_settings()

        if not smtp_settings.is_active:
            logger.warning("SMTP is not active. Cannot send broadcast.")
            return {
                "success": False,
                "error": "SMTP is not active. Please configure and enable SMTP settings.",
            }

        # Get recipient emails
        recipient_emails = get_recipient_emails(broadcast.target)

        if not recipient_emails:
            logger.warning(
                f"No recipients found for broadcast target: {broadcast.target}"
            )
            return {
                "success": False,
                "error": f"No recipients found for target: {broadcast.target}",
            }

        # Get SMTP connection
        connection = get_smtp_connection()

        # Prepare email
        from_email = (
            f"{smtp_settings.from_name} <{smtp_settings.from_email}>"
            if smtp_settings.from_name
            else smtp_settings.from_email
        )

        # Determine template based on language
        if language == "en":
            template_name = "subscriptions/broadcast_email_en.html"
        else:
            template_name = "subscriptions/broadcast_email.html"  # Default Arabic

        # Prepare context for template
        context = {"broadcast": broadcast, "from_name": smtp_settings.from_name}

        # Render HTML template
        html_content = render_to_string(template_name, context)

        # Create plain text version (fallback)
        plain_text = broadcast.content

        # Create email message
        email = EmailMultiAlternatives(
            subject=broadcast.subject,
            body=plain_text,
            from_email=from_email,
            to=recipient_emails,
            connection=connection,
        )

        # Attach HTML version
        email.attach_alternative(html_content, "text/html")

        # Send email
        email.send()

        logger.info(
            f"Broadcast {broadcast.id} sent successfully to {len(recipient_emails)} recipients"
        )

        return {
            "success": True,
            "recipients_count": len(recipient_emails),
            "recipients": recipient_emails,
        }

    except Exception as e:
        logger.error(f"Error sending broadcast email: {str(e)}")
        return {"success": False, "error": str(e)}
