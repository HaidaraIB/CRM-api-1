"""
Publishing side of the realtime channel.

Called from ``sync/signals.py``, next to the counter bumps, so the same receiver
that records "this changed" also announces it. Keeping both in one place is what
stops the two mechanisms drifting: a new model gets a bump and a publish together
or neither, rather than one silently without the other.

Two rules govern everything here.

**Never raise into the request path.** A publish is an optimisation — it makes a
change arrive in under a second instead of on the next poll. If Redis is
unreachable or the channel layer is misconfigured, the correct outcome is a
slightly slower CRM, not a failed write. Every entry point swallows its errors and
logs at debug level; the poll fallback is always underneath.

**Never carry data.** The frame says which scope moved and to what version, and
nothing else. Group membership is company-wide, but visibility inside a company is
not — WhatsApp chat and call access are per-user, and lead access is per-assignee.
A payload here would have to re-implement all of that filtering, in a second place,
with cross-user data leakage as the failure mode. Sending a version number means
the client re-reads through the ordinary REST endpoints, which already filter.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import transaction

from sync.version import company_slice_key, conversation_seq_key, user_seq_key

logger = logging.getLogger(__name__)

# Channel-layer group names. Kept here, in the module with no ``channels`` import
# of its own, so both the publisher and the consumer can name a group without
# either having to import the other.
COMPANY_GROUP = "company.{}"
USER_GROUP = "user.{}"
CONVERSATION_GROUP = "conversation.{}"

# Message type on the channel layer; maps to SyncConsumer.sync_event.
EVENT_TYPE = "sync.event"
# Maps to SyncConsumer.conversation_event — one open chat thread moved.
CONVERSATION_EVENT_TYPE = "conversation.event"
# Maps to SyncConsumer.presence_event — one peer's ephemeral activity.
PRESENCE_EVENT_TYPE = "presence.event"


def realtime_enabled() -> bool:
    """
    Read at call time, not import time, so tests and management commands can flip
    it with override_settings without reloading the module.
    """
    from django.conf import settings

    return bool(getattr(settings, "REALTIME_ENABLED", False))


def _channel_layer():
    """
    The configured channel layer, or None when realtime is not set up.

    Imported lazily so the rest of the app — management commands, the qcluster,
    tests — does not require ``channels`` to be installed just to save a model.
    """
    try:
        from channels.layers import get_channel_layer

        return get_channel_layer()
    except Exception:  # pragma: no cover - only when channels is absent
        return None


def _send_payload(group: str, payload: dict) -> None:
    """
    Hand one frame to the channel layer without blocking the caller's thread.

    The two branches are not an optimisation — the wrong one corrupts the request.

    Under WSGI (gunicorn in production, cron jobs, the qcluster) there is no event
    loop, so ``async_to_sync`` is the only way to await the layer, and it is safe.

    Under ASGI the view runs in a worker thread with the event loop live in
    another. ``async_to_sync`` there hands the coroutine to that loop and *blocks
    this thread waiting for it* — and Django tears down this thread's database
    connection across that boundary. The next ORM call in the same view then dies
    with "Cannot operate on a closed database", turning a successful write into a
    500 for a notification nobody was waiting on.

    So when a loop is running we schedule the send onto it and return immediately.
    Fire-and-forget is also the semantics we want everywhere: a publish is an
    optimisation over polling, and no write should ever wait on it.
    """
    layer = _channel_layer()
    if layer is None:
        return

    try:
        import asyncio

        from asgiref.sync import SyncToAsync, async_to_sync

        # Set by asgiref on threads it spawns for sync work inside ASGI. Its
        # presence is what distinguishes "called from an ASGI request" from
        # "called from gunicorn/cron/qcluster".
        loop = getattr(SyncToAsync.threadlocal, "main_event_loop", None)
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                layer.group_send(group, payload), loop
            )
            # Do not call future.result() — waiting is the whole problem. Attach a
            # callback so a failure is still visible instead of vanishing.
            future.add_done_callback(_log_publish_result)
            return

        async_to_sync(layer.group_send)(group, payload)
    except Exception as exc:
        # Deliberately debug, not warning: with no realtime process running this
        # would otherwise log on every write in the system.
        logger.debug("Realtime publish to %s failed (%s)", group, exc)


def _send(group: str, scope: str, version: int) -> None:
    """A version bump: the scope that moved, and to what. Carries no data."""
    _send_payload(group, {"type": EVENT_TYPE, "scope": scope, "version": version})


def _log_publish_result(future) -> None:
    try:
        future.result()
    except Exception as exc:
        logger.debug("Realtime publish failed (%s)", exc)


def publish_company_slice(slice_name: str, company_id) -> None:
    """
    Announce that a company slice moved.

    Deferred to ``on_commit`` so clients are never told to refetch data that is
    not yet visible — and never told at all if the transaction rolls back. The
    counter bump beside this one does not need the same treatment: it is
    monotonic, so an extra increment is harmless, whereas a premature publish
    causes a client to read stale rows and cache them as current.
    """
    if not company_id or not realtime_enabled():
        return

    def _publish():
        version = cache.get(company_slice_key(slice_name, company_id)) or 0
        _send(COMPANY_GROUP.format(company_id), f"company:{slice_name}", int(version))

    _on_commit(_publish)


def publish_user(user_id) -> None:
    """Announce that something changed for one user (notifications, read state)."""
    if not user_id or not realtime_enabled():
        return

    def _publish():
        version = cache.get(user_seq_key(user_id)) or 0
        _send(USER_GROUP.format(user_id), "user", int(version))

    _on_commit(_publish)


def publish_conversation(conversation_id) -> None:
    """
    Announce that one chat thread moved — a new message, or a read cursor.

    Addressed to the conversation group rather than the company one, because the
    people who care are exactly those with that thread open: they subscribed to
    it, so this reaches them and nobody else.

    This is what makes a read receipt live. The company ``tenant_chat`` slice is
    bumped only by ChatMessage writes, so before this the blue tick appeared when
    the *next message* happened to arrive — the thread's own poll was the only
    other route, and it backs off to 30s once a socket is up. A cursor moving is
    the one chat event that changes what the other participant sees while
    producing no message of its own.
    """
    if not conversation_id or not realtime_enabled():
        return

    def _publish():
        version = cache.get(conversation_seq_key(conversation_id)) or 0
        _send_payload(
            CONVERSATION_GROUP.format(conversation_id),
            {
                "type": CONVERSATION_EVENT_TYPE,
                "conversation": int(conversation_id),
                "version": int(version),
            },
        )

    _on_commit(_publish)


def publish_presence(conversation_id, user_id, state: str) -> None:
    """
    Fan out an ephemeral activity signal that arrived over HTTP.

    The consumer publishes its own frames for presence sent on the socket, but
    the POST endpoint is not a second-class path: mobile posts every typing state
    that way, and a client whose socket is down has nothing else. Without this the
    cache was written and no one was told, so a peer who had backed its presence
    poll off to 30s (because *its* socket was healthy) showed the indicator half a
    minute late or not at all.

    Not deferred to on_commit: presence touches no table, and its whole value is
    arriving now.
    """
    if not conversation_id or not user_id or not realtime_enabled():
        return
    _send_payload(
        CONVERSATION_GROUP.format(conversation_id),
        {
            "type": PRESENCE_EVENT_TYPE,
            "conversation": int(conversation_id),
            "user_id": int(user_id),
            "state": state,
        },
    )


def on_counter_changed(kind: str, **details) -> None:
    """
    Bridge from ``sync.version``'s change listeners to the channel layer.

    Registered in RealtimeConfig.ready(). Because it hooks the bump rather than the
    model signal, it also covers writes that signals cannot see — bulk updates, and
    the explicit invalidations in sync.cache — with no per-call-site wiring.
    """
    if kind == "company_slice":
        publish_company_slice(details.get("slice_name"), details.get("company_id"))
    elif kind == "user":
        publish_user(details.get("user_id"))
    elif kind == "conversation":
        publish_conversation(details.get("conversation_id"))


def _on_commit(fn) -> None:
    try:
        transaction.on_commit(fn)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not schedule realtime publish (%s)", exc)
