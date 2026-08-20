"""
Cache keys for GET /sync/digest/.

Kept separate from ``sync.views`` on purpose: the apps that clear an unread count
(notifications, tenant_chat, platform_content, integrations) need to invalidate
this cache, but ``sync.views`` imports *from* those apps to build the digest.
Importing it back would be a cycle, so the invalidation entry point lives here,
where the only dependency is Django's cache framework.
"""

from __future__ import annotations

from django.core.cache import cache

# How long a user's sidebar badge counts may lag. Only counts that no user is
# actively waiting on live in this tier — see the tiering note in sync/views.py.
BADGES_CACHE_TTL = 30
BADGES_CACHE_PREFIX = "sync_digest_badges_v1"


def badges_cache_key(user_id: int) -> str:
    return f"{BADGES_CACHE_PREFIX}:{user_id}"


def invalidate_badges(user_id: int) -> None:
    """
    Drop a user's cached badge counts.

    Call this from anything that clears an unread count (marking a chat,
    notification or news post read). Without it the sidebar badge would keep
    showing the old number for up to BADGES_CACHE_TTL seconds, right after the
    user acted on it.
    """
    if user_id:
        cache.delete(badges_cache_key(user_id))
