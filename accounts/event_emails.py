"""
Send transactional emails for important events (payment, subscription, support).
All emails are sent in the recipient's language (ar/en) using HTML templates.
"""
import logging
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from settings.models import SMTPSettings

from .utils import EMAIL_LANGUAGES, _get_smtp_connection

logger = logging.getLogger(__name__)


def _send_event_email(to_user, subject, template_name, context, language="en"):
    """
    Render template (language-specific), build email, and send.
    template_name: base name e.g. 'payment_success'; will use accounts/event_emails/<name>_ar.html or _en.html.
    """
    from django.template.loader import render_to_string

    lang = language if language in EMAIL_LANGUAGES else "en"
    suffix = "_ar" if lang == "ar" else "_en"
    full_name = f"accounts/event_emails/{template_name}{suffix}.html"
    html_content = render_to_string(full_name, context)
    plain_body = strip_tags(html_content)

    smtp_settings = SMTPSettings.get_settings()
    if not smtp_settings.is_active:
        logger.warning("SMTP is not active; skipping event email.")
        return False

    connection = _get_smtp_connection()
    from_email = (
        f"{smtp_settings.from_name} <{smtp_settings.from_email}>"
        if smtp_settings.from_name
        else smtp_settings.from_email
    )
    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_body,
        from_email=from_email,
        to=[to_user.email],
        connection=connection,
    )
    email.attach_alternative(html_content, "text/html")
    email.send()
    logger.info("Event email sent to %s: %s", to_user.email, template_name)
    return True


def send_payment_success_email(user, payment, subscription, language="en"):
    """Notify user (typically company owner) that a payment was successful."""
    plan_name = subscription.plan.name_ar if (language == "ar" and getattr(subscription.plan, "name_ar", None)) else subscription.plan.name
    amount_str = f"{payment.amount} {payment.currency}"
    if language == "ar":
        subject = "تم استلام دفعتك بنجاح - LOOP CRM"
    else:
        subject = "Payment received successfully - LOOP CRM"
    context = {
        "greeting_name": user.first_name or user.username or ("مرحباً" if language == "ar" else "there"),
        "plan_name": plan_name,
        "amount": amount_str,
        "support_email": SMTPSettings.get_settings().from_email,
    }
    return _send_event_email(user, subject, "payment_success", context, language)


def send_payment_failed_email(user, payment, subscription, language="en", reason=None):
    """Notify user that a payment failed."""
    plan_name = subscription.plan.name_ar if (language == "ar" and getattr(subscription.plan, "name_ar", None)) else subscription.plan.name
    if language == "ar":
        subject = "فشل عملية الدفع - LOOP CRM"
    else:
        subject = "Payment failed - LOOP CRM"
    context = {
        "greeting_name": user.first_name or user.username or ("مرحباً" if language == "ar" else "there"),
        "plan_name": plan_name,
        "reason": reason or ("يرجى المحاولة مرة أخرى أو اختيار طريقة دفع أخرى." if language == "ar" else "Please try again or use another payment method."),
        "support_email": SMTPSettings.get_settings().from_email,
    }
    return _send_event_email(user, subject, "payment_failed", context, language)


def send_subscription_expiring_email(user, subscription, days_remaining, language="en"):
    """Notify user that their subscription is expiring soon."""
    plan_name = subscription.plan.name_ar if (language == "ar" and getattr(subscription.plan, "name_ar", None)) else subscription.plan.name
    end_date_str = subscription.end_date.strftime("%Y-%m-%d %H:%M")
    if language == "ar":
        subject = f"تذكير: انتهاء اشتراكك قريباً - LOOP CRM"
    else:
        subject = "Reminder: Your subscription is ending soon - LOOP CRM"
    context = {
        "greeting_name": user.first_name or user.username or ("مرحباً" if language == "ar" else "there"),
        "plan_name": plan_name,
        "days_remaining": days_remaining,
        "end_date": end_date_str,
        "support_email": SMTPSettings.get_settings().from_email,
    }
    return _send_event_email(user, subject, "subscription_expiring", context, language)


def send_subscription_expired_email(user, subscription, language="en"):
    """Notify user that their subscription has expired."""
    plan_name = subscription.plan.name_ar if (language == "ar" and getattr(subscription.plan, "name_ar", None)) else subscription.plan.name
    if language == "ar":
        subject = "انتهى اشتراكك - LOOP CRM"
    else:
        subject = "Your subscription has expired - LOOP CRM"
    context = {
        "greeting_name": user.first_name or user.username or ("مرحباً" if language == "ar" else "there"),
        "plan_name": plan_name,
        "support_email": SMTPSettings.get_settings().from_email,
    }
    return _send_event_email(user, subject, "subscription_expired", context, language)


def send_support_ticket_created_email(user, ticket, language="en"):
    """Confirm to the user that their support ticket was created."""
    if language == "ar":
        subject = "تم إنشاء تذكرة الدعم بنجاح - LOOP CRM"
    else:
        subject = "Support ticket created - LOOP CRM"
    context = {
        "greeting_name": user.first_name or user.username or ("مرحباً" if language == "ar" else "there"),
        "ticket_title": ticket.title,
        "ticket_id": ticket.id,
        "support_email": SMTPSettings.get_settings().from_email,
    }
    return _send_event_email(user, subject, "support_ticket_created", context, language)
