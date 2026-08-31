"""
Cache keys for GET /sync/digest/.

Kept separate from ``sync.views`` on purpose: the apps that clear an unread count
(notifications, tenant_chat, platform_content, integrations) need to invalidate
this cache, but ``sync.views`` imports *from* those apps to build the digest.
Importing it back would be a cycle, so the invalidation entry point lives here,
where the only dependency is the version counters.
"""

from __future__ import annotations

from .version import SAFETY_BUCKET_SECONDS, bump_user

# The cache key embeds the version token, which already carries a time bucket, so
# an entry is only ever reachable for one bucket. This TTL just reaps the orphans
# a rotation leaves behind.
BADGES_CACHE_TTL = SAFETY_BUCKET_SECONDS * 2
BADGES_CACHE_PREFIX = "sync_digest_badges_v2"


def badges_cache_key(user_id: int, token: str) -> str:
    """
    Per-user badge counts, keyed by the version token that produced them.

    Keying on the token rather than the user alone is what makes company-wide
    events safe to cache: a teammate's message bumps the company counter, every
    key derived from it becomes unreachable at once, and nobody has to enumerate
    the affected users to expire them one by one.
    """
    return f"{BADGES_CACHE_PREFIX}:{user_id}:{token}"


def invalidate_badges(user_id: int) -> None:
    """
    Drop a user's cached badge counts.

    Call this from anything that clears an unread count (marking a chat,
    notification or news post read). Bumping the user's counter rotates both the
    badge cache key and the digest ETag, so the next poll rebuilds instead of
    being told nothing changed — without it the sidebar would keep showing the
    old number right after the user acted on it.
    """
    bump_user(user_id)
