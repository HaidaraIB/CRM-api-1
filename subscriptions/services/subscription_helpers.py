"""Subscription domain helpers (single active subscription per company)."""
import logging

from ..models import Subscription

logger = logging.getLogger(__name__)


def deactivate_other_subscriptions_for_company(company_id, exclude_subscription_id=None):
    """Ensure only one subscription per company is active: deactivate all others for this company."""
    qs = Subscription.objects.filter(company_id=company_id)
    if exclude_subscription_id is not None:
        qs = qs.exclude(pk=exclude_subscription_id)
    updated = qs.filter(is_active=True).update(is_active=False)
    if updated:
        logger.info(
            "Deactivated %s other subscription(s) for company_id=%s (only one active per company)",
            updated,
            company_id,
        )
