"""
Utility functions for sending emails via SMTP and push notifications
"""

from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend
from django.template.loader import render_to_string
from settings.models import SMTPSettings
from companies.models import Company
from accounts.models import User, Role
from subscriptions.models import Subscription
from notifications.services import NotificationService
from notifications.models import NotificationType
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


def get_recipient_users(broadcast_target):
    """
    Get list of recipient users based on broadcast target
    
    Args:
        broadcast_target: Can be "all" or a plan ID (e.g., "plan_1", "plan_2")
    
    Returns:
        QuerySet of User objects
    """
    users = User.objects.none()  # Empty queryset by default
    
    if broadcast_target == "all":
        # Get all admin users from all companies
        users = User.objects.filter(role=Role.ADMIN.value, is_active=True)
    elif broadcast_target.startswith("plan_"):
        # Extract plan ID from target (e.g., "plan_1" -> 1)
        try:
            plan_id = int(broadcast_target.replace("plan_", ""))
            
            # Get all companies subscribed to this plan
            subscriptions = Subscription.objects.filter(plan_id=plan_id, is_active=True)
            company_ids = subscriptions.values_list("company_id", flat=True)
            companies = Company.objects.filter(id__in=company_ids)
            
            # Get admin users from these companies
            users = User.objects.filter(
                company__in=companies,
                role=Role.ADMIN.value,
                is_active=True
            )
        except (ValueError, TypeError):
            # Invalid plan ID, return empty queryset
            return User.objects.none()
    
    # Filter users with FCM tokens (only users who can receive push notifications)
    users = users.exclude(fcm_token__isnull=True).exclude(fcm_token='')
    
    return users


def send_broadcast_push_notification(broadcast):
    """
    Send broadcast push notification to recipients based on target
    
    Args:
        broadcast: Broadcast instance
    """
    try:
        # Initialize Firebase if not already initialized
        NotificationService.initialize()
        
        # Get recipient users
        users = get_recipient_users(broadcast.target)
        users_list = list(users)
        
        if not users_list:
            logger.warning(
                f"No recipients with FCM tokens found for broadcast target: {broadcast.target}"
            )
            return {
                "success": False,
                "error": f"No recipients with push notification tokens found for target: {broadcast.target}",
            }
        
        # Send notification to each user
        success_count = 0
        failed_count = 0
        
        for user in users_list:
            try:
                # Use BROADCAST notification type
                result = NotificationService.send_notification(
                    user=user,
                    notification_type=NotificationType.BROADCAST.value,
                    title=broadcast.subject,
                    body=broadcast.content,
                    data={
                        'broadcast_id': str(broadcast.id),
                        'type': 'broadcast',
                    },
                    skip_settings_check=True,  # Broadcasts should bypass user settings
                )
                
                if result:
                    success_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                logger.error(f"Error sending push notification to user {user.id}: {str(e)}")
                failed_count += 1
        
        logger.info(
            f"Broadcast {broadcast.id} push notifications sent: {success_count} success, {failed_count} failed"
        )
        
        if success_count > 0:
            return {
                "success": True,
                "recipients_count": success_count,
                "failed_count": failed_count,
            }
        else:
            return {
                "success": False,
                "error": "Failed to send push notifications to all recipients",
                "failed_count": failed_count,
            }
    
    except Exception as e:
        logger.error(f"Error sending broadcast push notification: {str(e)}")
        return {"success": False, "error": str(e)}
