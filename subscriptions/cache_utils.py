"""Invalidate permission caches tied to subscription state."""

from django.core.cache import cache


def invalidate_company_subscription_cache(company_id: int) -> None:
    if company_id is None:
        return
    cache.delete(f"active_sub_{company_id}")
