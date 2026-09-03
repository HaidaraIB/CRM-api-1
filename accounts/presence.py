"""
Who is online, from two sources that answer the same question.

Presence used to be inferred entirely from ``User.last_seen_at``: the web app
POSTed a heartbeat every 60s and anyone seen within 90s counted as online. That
works, but it is both slow and expensive — someone who just opened the CRM looks
offline for up to a minute, and every user in the company writes a row a minute
to say "still here".

An open WebSocket already *is* presence, so it is used as the primary signal and
recorded in the cache. ``last_seen_at`` stays as the fallback, which matters:
the mobile app has no socket, and a browser with realtime disabled has none
either. Online therefore means **live socket OR recently seen**, and turning
realtime off degrades this to exactly the previous behaviour.

The database column keeps being written, but at a fraction of the rate — see
``touch_last_seen``. It still backs "last seen 3 hours ago" displays and reports,
which a cache with a 90-second TTL cannot.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

# Unchanged from the four places that previously hard-coded it, so a user's
# online/offline status means the same thing it always did.
ONLINE_WINDOW = timedelta(seconds=90)

LIVE_PREFIX = "presence:live:v1"
# Slightly longer than the client's heartbeat cadence so one missed frame does
# not blink someone offline.
LIVE_TTL_SECONDS = 90

DB_THROTTLE_PREFIX = "presence:db:v1"
# How often a live connection is allowed to write last_seen_at. The column only
# needs to be good enough for "last seen" text and reports; the cache is what
# answers "online right now".
DB_WRITE_INTERVAL_SECONDS = 300


def _live_key(user_id: int) -> str:
    return f"{LIVE_PREFIX}:{user_id}"


def mark_live(user_id: int) -> None:
    """Record that this user currently holds an open socket."""
    if not user_id:
        return
    cache.set(_live_key(user_id), 1, LIVE_TTL_SECONDS)


def clear_live(user_id: int) -> None:
    """
    Drop the live marker on disconnect.

    Not sufficient on its own to show someone offline, and deliberately so: the
    ``last_seen_at`` fallback still covers them for the rest of its window, which
    is what stops a browser refresh — disconnect, reconnect a second later —
    flickering the green dot for everyone watching.
    """
    if not user_id:
        return
    cache.delete(_live_key(user_id))


def live_user_ids(user_ids) -> set[int]:
    """
    Which of these users hold a socket, in one cache round trip.

    Bulk by default because the callers are list endpoints — asking per user
    would put one Redis round trip per row into every user list and chat roster
    in the product.
    """
    ids = [uid for uid in user_ids if uid]
    if not ids:
        return set()
    keys = {_live_key(uid): uid for uid in ids}
    found = cache.get_many(list(keys))
    return {keys[key] for key in found}


def touch_last_seen(user_id: int, source: str = "web") -> None:
    """
    Keep ``last_seen_at`` roughly current from a socket, without a write per beat.

    Throttled through the cache: a live connection heartbeats every 45s, but the
    column is only written every few minutes. Without this, moving presence onto
    the socket would keep exactly the write volume it was supposed to remove.
    """
    if not user_id:
        return
    throttle_key = f"{DB_THROTTLE_PREFIX}:{user_id}"
    if cache.get(throttle_key):
        return
    cache.set(throttle_key, 1, DB_WRITE_INTERVAL_SECONDS)

    from django.contrib.auth import get_user_model

    get_user_model().objects.filter(pk=user_id).update(
        last_seen_at=timezone.now(), last_seen_source=source
    )


def is_online(user, live_ids: set[int] | None = None) -> bool:
    """
    Online = holds a socket, or was seen within the window.

    ``live_ids`` lets a caller serializing many users pass a set built with one
    ``live_user_ids`` call; without it this falls back to a single cache read,
    which is correct but should not be used inside a loop.
    """
    user_id = getattr(user, "id", None)
    if user_id:
        if live_ids is not None:
            if user_id in live_ids:
                return True
        elif cache.get(_live_key(user_id)):
            return True

    last_seen = getattr(user, "last_seen_at", None)
    if not last_seen:
        return False
    return (timezone.now() - last_seen) <= ONLINE_WINDOW
