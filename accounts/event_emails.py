"""
Send transactional emails for important events (payment, subscription, support).
All emails are sent in the recipient's language (ar/en) using HTML templates.
"""
import logging
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from crm_saas_api.utils import format_platform_from_address
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
        logger.warning("Outbound email is not active; skipping event email.")
        return False

    connection = _get_smtp_connection()
    from_email = format_platform_from_address(smtp_settings)
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


def _support_ticket_description_preview(ticket, max_len=400):
    text = (getattr(ticket, "description", None) or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def send_support_ticket_new_admin_notifications(creator_user, ticket):
    """
    Email all active Django superusers (platform super admins) about a new ticket.
    Skips any super admin whose email matches the creator so they are not notified twice
    (they already receive send_support_ticket_created_email).
    """
    from accounts.models import User

    smtp_settings = SMTPSettings.get_settings()
    if not smtp_settings.is_active:
        logger.warning(
            "Outbound email is not active; skipping super-admin support ticket notifications."
        )
        return 0

    creator_email = (getattr(creator_user, "email", None) or "").strip().lower()
    company = getattr(ticket, "company", None)
    company_name = getattr(company, "name", "") or "—"
    preview = _support_ticket_description_preview(ticket)
    creator_display = creator_user.get_full_name().strip() or creator_user.username
    creator_line = f"{creator_display} <{creator_user.email}>"

    admins = User.objects.filter(is_superuser=True, is_active=True).exclude(
        email=""
    )
    sent = 0
    seen = set()
    for admin in admins:
        em = (admin.email or "").strip().lower()
        if not em or em in seen:
            continue
        if em == creator_email:
            continue
        seen.add(em)
        lang = (getattr(admin, "language", None) or "en").lower()
        if lang not in EMAIL_LANGUAGES:
            lang = "en"
        if lang == "ar":
            subject = f"تذكرة دعم جديدة #{ticket.id} - LOOP CRM"
        else:
            subject = f"New support ticket #{ticket.id} - LOOP CRM"
        context = {
            "greeting_name": admin.first_name or admin.username
            or ("مرحباً" if lang == "ar" else "there"),
            "ticket_title": ticket.title,
            "ticket_id": ticket.id,
            "company_name": company_name,
            "creator_line": creator_line,
            "description_preview": preview,
            "support_email": smtp_settings.from_email,
        }
        if _send_event_email(
            admin, subject, "support_ticket_new_admin", context, lang
        ):
            sent += 1
    return sent


def send_followup_reminder_email(
    user,
    *,
    reminder_kind: str,
    title: str,
    scheduled_for,
    minutes_before: int = 15,
    lead_name: str = None,
    extra_line: str = None,
    language: str = "en",
):
    """
    Send a follow-up reminder email (generic for lead/task/call).

    reminder_kind: one of 'lead', 'task', 'call', 'deal' (used only for display).
    title: short label shown in the email (e.g. task title / lead name / call reminder).
    scheduled_for: datetime of the original reminder time.
    """
    try:
        lang = language if language in EMAIL_LANGUAGES else "en"
        if lang == "ar":
            subject = "تذكير متابعة - LOOP CRM"
        else:
            subject = "Follow-up reminder - LOOP CRM"

        when_str = ""
        try:
            when_str = scheduled_for.strftime("%Y-%m-%d %H:%M") if scheduled_for else ""
        except Exception:
            when_str = str(scheduled_for) if scheduled_for else ""

        context = {
            "greeting_name": user.first_name or user.username or ("مرحباً" if lang == "ar" else "there"),
            "reminder_kind": reminder_kind,
            "title": title,
            "lead_name": lead_name,
            "minutes_before": minutes_before,
            "scheduled_for": when_str,
            "extra_line": extra_line,
            "support_email": SMTPSettings.get_settings().from_email,
        }
        return _send_event_email(user, subject, "followup_reminder", context, lang)
    except Exception as exc:
        logger.error("Failed to send followup reminder email: %s", exc)
        return False
