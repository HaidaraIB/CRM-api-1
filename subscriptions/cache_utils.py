"""Invalidate permission caches tied to subscription state."""

from django.core.cache import cache


def invalidate_company_subscription_cache(company_id: int) -> None:
    if company_id is None:
        return
    cache.delete(f"active_sub_{company_id}")

    # The per-platform plan gate is derived from the same subscription, so a plan
    # change has to drop it here too — otherwise an upgrade would not open the
    # integration until its TTL lapsed. Imported locally: integrations.policy
    # imports from subscriptions.entitlements, so a module-level import here
    # would close the loop.
    from integrations.policy import PLAN_INTEGRATION_FEATURE_MAP, plan_access_cache_key

    cache.delete_many(
        [plan_access_cache_key(company_id, platform) for platform in PLAN_INTEGRATION_FEATURE_MAP]
    )
