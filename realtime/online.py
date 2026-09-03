"""
Async wrappers around the online-presence cache, for the consumer.

Kept separate from ``accounts/presence.py`` because the direction of the
dependency matters: the accounts helpers are read by serializers, the dashboard
and the REST API, none of which should have to know that channels exists. This
module is the only place the two meet.
"""

from __future__ import annotations

from channels.db import database_sync_to_async


@database_sync_to_async
def go_live(user_id: int) -> None:
    """
    Mark the user online, and let their ``last_seen_at`` catch up occasionally.

    Both calls are cheap: the first is a cache write, the second usually does
    nothing at all because it is throttled to one database write every few
    minutes. Wrapped in database_sync_to_async because that throttled path can
    touch the database.
    """
    from accounts.presence import mark_live, touch_last_seen

    mark_live(user_id)
    touch_last_seen(user_id, source="web")


@database_sync_to_async
def go_offline(user_id: int) -> None:
    """
    Drop the live marker on disconnect.

    Does not by itself make the user look offline — the ``last_seen_at`` window
    still covers them — which is deliberate: a page refresh disconnects and
    reconnects within a second, and nobody watching should see the green dot
    blink.
    """
    from accounts.presence import clear_live

    clear_live(user_id)
