"""
Signals for subscription-related events (e.g. payment completed -> send email).
"""
import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Payment, PaymentStatus

logger = logging.getLogger(__name__)

# Store previous payment_status in pre_save so post_save can detect transition to COMPLETED
_previous_payment_status = {}


@receiver(pre_save, sender=Payment)
def _store_previous_payment_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = Payment.objects.filter(pk=instance.pk).values_list("payment_status", flat=True).first()
            _previous_payment_status[instance.pk] = old
        except Exception:
            pass


def _get_owner_and_subscription(payment):
    try:
        subscription = payment.subscription
        if not subscription or not subscription.company:
            return None, None
        owner = getattr(subscription.company, "owner", None)
        return owner, subscription
    except Exception:
        return None, None


@receiver(post_save, sender=Payment)
def on_payment_saved(sender, instance, created, **kwargs):
    """When payment status becomes COMPLETED or FAILED, send email to subscription owner in their language."""
    prev = _previous_payment_status.pop(instance.pk, None)
    just_completed = (
        instance.payment_status == PaymentStatus.COMPLETED.value
        and (created or prev != PaymentStatus.COMPLETED.value)
    )
    just_failed = (
        instance.payment_status == PaymentStatus.FAILED.value
        and (created or prev != PaymentStatus.FAILED.value)
    )

    if not just_completed and not just_failed:
        return

    owner, subscription = _get_owner_and_subscription(instance)
    if not owner or not getattr(owner, "email", None) or not subscription:
        return

    try:
        from accounts.event_emails import send_payment_success_email, send_payment_failed_email
        from accounts.utils import get_email_language_for_user

        # Ensure we have the latest language preference from DB
        try:
            owner.refresh_from_db(fields=["language"])
        except Exception:
            pass
        language = get_email_language_for_user(owner, request=None, default="en")
        if just_completed:
            send_payment_success_email(owner, instance, subscription, language=language)
        elif just_failed:
            send_payment_failed_email(owner, instance, subscription, language=language)
    except Exception as e:
        logger.exception("Failed to send payment event email: %s", e)
