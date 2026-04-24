"""
Utility functions for sending emails via Resend and push notifications
"""

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from crm_saas_api.utils import format_platform_from_address, get_platform_email_display_name
from settings.models import SMTPSettings, PlatformTwilioSettings
from companies.models import Company
from accounts.models import User, Role
from subscriptions.models import Subscription
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


def get_smtp_connection():
    """Get outbound email connection (Resend) using platform SMTPSettings singleton."""
    from crm_saas_api.utils import get_smtp_connection as _get_smtp
    return _get_smtp()


def get_broadcast_targets_list(broadcast):
    """Return list of target strings for a broadcast."""
    targets = getattr(broadcast, "targets", None)
    if targets and len(targets) > 0:
        return list(targets)
    return ["all"]


def get_recipient_emails(broadcast_target):
    """
    Get list of recipient email addresses based on broadcast target.

    Args:
        broadcast_target: One of:
            - "all": admins of all companies
            - "plan_{id}": admins of companies on that plan
            - "role_admin" / "role_supervisor" / "role_employee": all active users with that role
            - "company_{id}": all active users in that company
    """
    users = _get_eligible_recipient_users_queryset(broadcast_target)
    emails = [u.email for u in users if u.email]
    return list(set(emails))


def get_recipient_emails_for_broadcast(broadcast):
    """Union of recipient emails for all of the broadcast's targets."""
    targets = get_broadcast_targets_list(broadcast)
    all_emails = []
    for t in targets:
        all_emails.extend(get_recipient_emails(t))
    return list(set(all_emails))


def get_recipient_users_for_email_broadcast(broadcast):
    """
    Return list of User objects (with email) for all of the broadcast's targets, deduplicated.
    Used to send each recipient the broadcast in their preferred language.
    """
    targets = get_broadcast_targets_list(broadcast)
    seen_ids = set()
    result = []
    for t in targets:
        users = _get_eligible_recipient_users_queryset(t)
        for u in users:
            if u.email and u.id not in seen_ids:
                seen_ids.add(u.id)
                result.append(u)
    return result


def send_broadcast_email(broadcast, language=None):
    """
    Send broadcast email to recipients based on target.
    Each recipient receives the email in their chosen language (user.language).
    If language is passed (e.g. from admin), it is used as fallback when user has no preference.

    Args:
        broadcast: Broadcast instance
        language: Optional override; if None, each user gets email in their preferred language.
    """
    try:
        smtp_settings = SMTPSettings.get_settings()

        if not smtp_settings.is_active:
            logger.warning("Outbound email is not active. Cannot send broadcast.")
            return {
                "success": False,
                "error": "Outbound email is not active. Enable it in platform email settings and set RESEND_API_KEY.",
            }

        # Get recipient users (with email) so we can use each user's language
        recipient_users = get_recipient_users_for_email_broadcast(broadcast)
        targets_list = get_broadcast_targets_list(broadcast)

        if not recipient_users:
            logger.warning(
                f"No recipients found for broadcast targets: {targets_list}"
            )
            return {
                "success": False,
                "error": f"No recipients found for target(s): {', '.join(targets_list)}",
            }

        connection = get_smtp_connection()
        from_email = format_platform_from_address(smtp_settings)
        display_name = get_platform_email_display_name(smtp_settings)
        default_lang = language if language in ("ar", "en") else "ar"

        sent_count = 0
        recipient_emails = []
        for user in recipient_users:
            # Use user's chosen language, then optional override, then default
            user_lang = getattr(user, "language", None)
            if user_lang in ("ar", "en"):
                lang = user_lang
            else:
                lang = default_lang

            if lang == "en":
                template_name = "subscriptions/broadcast_email_en.html"
            else:
                template_name = "subscriptions/broadcast_email.html"

            context = {"broadcast": broadcast, "from_name": display_name}
            html_content = render_to_string(template_name, context)
            plain_text = broadcast.content

            email_msg = EmailMultiAlternatives(
                subject=broadcast.subject,
                body=plain_text,
                from_email=from_email,
                to=[user.email],
                connection=connection,
            )
            email_msg.attach_alternative(html_content, "text/html")
            email_msg.send()
            sent_count += 1
            recipient_emails.append(user.email)

        logger.info(
            f"Broadcast {broadcast.id} sent successfully to {sent_count} recipients (per-user language)"
        )

        return {
            "success": True,
            "recipients_count": sent_count,
            "recipients": recipient_emails,
        }

    except Exception as e:
        logger.error(f"Error sending broadcast email: {str(e)}")
        return {"success": False, "error": str(e)}


def _get_eligible_recipient_users_queryset(broadcast_target):
    """
    Get eligible recipient users by target (before FCM token filter).

    Supports: "all", "plan_{id}", "role_admin", "role_supervisor", "role_employee", "company_{id}".
    """
    users = User.objects.none()
    if broadcast_target == "all":
        # Admins of all companies (legacy: same as role_admin for companies with subscriptions)
        users = User.objects.filter(role=Role.ADMIN.value, is_active=True)
    elif broadcast_target.startswith("plan_"):
        try:
            plan_id = int(broadcast_target.replace("plan_", ""))
            subscriptions = Subscription.objects.filter(plan_id=plan_id, is_active=True)
            company_ids = subscriptions.values_list("company_id", flat=True)
            companies = Company.objects.filter(id__in=company_ids)
            users = User.objects.filter(
                company__in=companies,
                role=Role.ADMIN.value,
                is_active=True,
            )
        except (ValueError, TypeError):
            return User.objects.none()
    elif broadcast_target == "role_admin":
        users = User.objects.filter(role=Role.ADMIN.value, is_active=True)
    elif broadcast_target == "role_supervisor":
        users = User.objects.filter(role=Role.SUPERVISOR.value, is_active=True)
    elif broadcast_target == "role_employee":
        users = User.objects.filter(role=Role.EMPLOYEE.value, is_active=True)
    elif broadcast_target.startswith("company_"):
        try:
            company_id = int(broadcast_target.replace("company_", ""))
            users = User.objects.filter(company_id=company_id, is_active=True)
        except (ValueError, TypeError):
            return User.objects.none()
    return users


def get_recipient_users(broadcast_target):
    """
    Get list of recipient users based on broadcast target (only users with FCM token).
    
    Args:
        broadcast_target: Can be "all" or a plan ID (e.g., "plan_1", "plan_2")
    
    Returns:
        QuerySet of User objects
    """
    users = _get_eligible_recipient_users_queryset(broadcast_target)
    users = users.exclude(fcm_token__isnull=True).exclude(fcm_token='')
    return users


def get_recipient_users_for_broadcast(broadcast):
    """Union of recipient users (with FCM token) for all of the broadcast's targets. Returns list of User, deduplicated."""
    targets = get_broadcast_targets_list(broadcast)
    seen_ids = set()
    result = []
    for t in targets:
        for user in get_recipient_users(t):
            if user.id not in seen_ids:
                seen_ids.add(user.id)
                result.append(user)
    return result


def _get_eligible_count_for_broadcast(broadcast):
    """Total eligible users (before FCM filter) across all targets, deduplicated by user id."""
    targets = get_broadcast_targets_list(broadcast)
    seen_ids = set()
    count = 0
    for t in targets:
        qs = _get_eligible_recipient_users_queryset(t)
        for u in qs.values_list("id", flat=True):
            if u not in seen_ids:
                seen_ids.add(u)
                count += 1
    return count


def send_broadcast_push_notification(broadcast):
    """
    Send broadcast push notification to recipients based on target
    
    Args:
        broadcast: Broadcast instance
    """
    try:
        # Initialize Firebase if not already initialized
        NotificationService.initialize()
        
        # Get eligible users and those with FCM token (supports multiple targets)
        targets_list = get_broadcast_targets_list(broadcast)
        eligible_count = _get_eligible_count_for_broadcast(broadcast)
        users_list = get_recipient_users_for_broadcast(broadcast)
        skipped_no_token = max(0, eligible_count - len(users_list))
        
        if not users_list:
            logger.warning(
                f"No recipients with FCM tokens found for broadcast targets: {targets_list} "
                f"(eligible: {eligible_count}, skipped_no_token: {skipped_no_token})"
            )
            return {
                "success": False,
                "error": f"No recipients with push notification tokens found for target(s): {', '.join(targets_list)}. "
                         f"{skipped_no_token} user(s) have no FCM token (ask them to open the app on their device).",
                "eligible_count": eligible_count,
                "skipped_no_token": skipped_no_token,
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
                "skipped_no_token": skipped_no_token,
                "eligible_count": eligible_count,
            }
        else:
            return {
                "success": False,
                "error": "Failed to send push notifications to all recipients",
                "failed_count": failed_count,
                "skipped_no_token": skipped_no_token,
                "eligible_count": eligible_count,
            }
    
    except Exception as e:
        logger.error(f"Error sending broadcast push notification: {str(e)}")
        return {"success": False, "error": str(e)}


def _normalize_phone_for_sms(phone):
    """Normalize phone to E.164 (e.g. 07... -> +964...)."""
    if not phone:
        return None
    to = str(phone).strip().replace(" ", "").replace("-", "")
    if not to:
        return None
    if to.startswith("07") and len(to) >= 10:
        to = "+964" + to[1:]
    elif not to.startswith("+"):
        to = "+" + to
    return to


def get_recipient_users_with_phone_for_targets(targets):
    """
    Return list of User objects that have a non-empty phone, for the given targets.
    Same targeting as broadcast: all, plan_X, role_*, company_X. Deduplicated by user id.
    """
    if not targets:
        targets = ["all"]
    seen_ids = set()
    result = []
    for t in targets:
        users = _get_eligible_recipient_users_queryset(t)
        for u in users:
            if u.id not in seen_ids and u.phone and str(u.phone).strip():
                seen_ids.add(u.id)
                result.append(u)
    return result


def send_broadcast_sms(targets, content):
    """
    Send SMS to all users with phone numbers matching the given targets, using platform Twilio.
    targets: list of target strings (e.g. ["all"], ["company_1", "plan_2"]).
    content: message body.
    Returns: dict with success, sent_count, skipped_count, error (if any).
    """
    try:
        twilio_settings = PlatformTwilioSettings.get_settings()
        if not twilio_settings.is_enabled:
            return {
                "success": False,
                "error": "Platform SMS is not enabled. Configure Twilio in Settings.",
                "sent_count": 0,
                "skipped_count": 0,
            }
        account_sid = twilio_settings.account_sid
        auth_token = twilio_settings.get_auth_token()
        twilio_number = twilio_settings.twilio_number
        sender_id = (twilio_settings.sender_id or "").strip()
        from_value = sender_id if sender_id else (twilio_number or "")
        if not account_sid or not auth_token or not from_value:
            return {
                "success": False,
                "error": "Twilio credentials incomplete. Set Account SID, Auth Token, and sender number or Sender ID.",
                "sent_count": 0,
                "skipped_count": 0,
            }
        users = get_recipient_users_with_phone_for_targets(targets)
        if not users:
            return {
                "success": False,
                "error": "No recipients with phone numbers found for the selected targets.",
                "sent_count": 0,
                "skipped_count": 0,
            }
        from twilio.rest import Client as TwilioClient
        from twilio.base.exceptions import TwilioRestException

        client = TwilioClient(account_sid, auth_token)
        sent_count = 0
        failed_count = 0
        for user in users:
            to = _normalize_phone_for_sms(user.phone)
            if not to:
                continue
            try:
                client.messages.create(body=content, from_=from_value, to=to)
                sent_count += 1
            except TwilioRestException as e:
                logger.warning("Twilio SMS to %s failed: %s", to, e)
                failed_count += 1
        if sent_count > 0:
            return {
                "success": True,
                "sent_count": sent_count,
                "skipped_count": failed_count,
                "error": None,
            }
        return {
            "success": False,
            "error": "Failed to send to any recipient. Check Twilio settings and phone numbers.",
            "sent_count": 0,
            "skipped_count": failed_count,
        }
    except Exception as e:
        logger.exception("send_broadcast_sms failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "sent_count": 0,
            "skipped_count": 0,
        }
