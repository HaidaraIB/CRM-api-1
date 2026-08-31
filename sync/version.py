"""
Version counters behind the ETag on GET /sync/digest/.

The digest is polled every few seconds by every open tab, so the common answer —
"nothing has changed since your last poll" — has to be produced without touching
the database. These counters make that possible: every write path that can move
one of the digest's counts bumps an integer in the cache, and the endpoint folds
the current values into an opaque token. A client that sends that token back in
``If-None-Match`` gets a bare 304 for the price of a single cache read.

Scopes mirror how the underlying counts are shared:

    global   — news posts, which are platform-wide.
    company  — chats, calls, arrivals: anything another user in the same tenant
               can cause to change for you.
    user     — notifications and read cursors, which are yours alone.

The token also carries a coarse time bucket, and that is the safety net. If a
write path is ever added without a bump, or the cache loses a counter, the token
still rotates on its own and the digest rebuilds. Staleness is therefore capped
at SAFETY_BUCKET_SECONDS instead of lasting until the next unrelated event —
which is the same freshness the badge tier has always had.

The bumps themselves live in ``sync/signals.py``, on model signals rather than in
view code, so a new write path cannot silently skip one.
"""

from __future__ import annotations

import time

from django.core.cache import cache

# Counters outlive any single request but should not accumulate forever for users
# and companies that go away. The time bucket means a dropped counter costs one
# bucket of staleness, not a stuck client, so this TTL can be generous.
SEQ_TTL = 60 * 60 * 24 * 30

# How long the digest may go without a rebuild when no bump ever fires. Matches
# the lag the badge tier has always tolerated.
SAFETY_BUCKET_SECONDS = 30

GLOBAL_SEQ_KEY = "sync_seq_global_v1"
COMPANY_SEQ_PREFIX = "sync_seq_company_v1"
USER_SEQ_PREFIX = "sync_seq_user_v1"


def company_seq_key(company_id: int) -> str:
    return f"{COMPANY_SEQ_PREFIX}:{company_id}"


def user_seq_key(user_id: int) -> str:
    return f"{USER_SEQ_PREFIX}:{user_id}"


def _bump(key: str) -> None:
    try:
        cache.incr(key)
    except ValueError:
        # incr() raises when the key is absent, so seed it. Two writers racing
        # here can both land on 1, losing one increment — harmless, because the
        # token only has to *change*, it does not have to count events.
        cache.set(key, 1, SEQ_TTL)


def bump_global() -> None:
    """Something platform-wide changed (a news post)."""
    _bump(GLOBAL_SEQ_KEY)


def bump_company(company_id) -> None:
    """Something changed that any user in this tenant may need to see."""
    if company_id:
        _bump(company_seq_key(company_id))


def bump_user(user_id) -> None:
    """Something changed that only this user sees."""
    if user_id:
        _bump(user_seq_key(user_id))


def digest_token(user) -> str:
    """
    Current version token for this user's digest.

    One ``get_many`` (a single MGET on Redis) and no database access, which is the
    whole point — this runs on every poll, including the ones that 304.
    """
    company_id = getattr(user, "company_id", None)
    keys = [GLOBAL_SEQ_KEY, user_seq_key(user.id)]
    if company_id:
        keys.append(company_seq_key(company_id))

    values = cache.get_many(keys)
    # Leading user id so two users can never hold the same token. Counters are
    # per-user and start at the same place, so without it a client that kept a
    # token across a logout could be 304'd against the previous user's digest and
    # show their counts.
    parts = [str(user.id)]
    parts.extend(str(values.get(key) or 0) for key in keys)
    parts.append(str(int(time.time() // SAFETY_BUCKET_SECONDS)))
    return ".".join(parts)
