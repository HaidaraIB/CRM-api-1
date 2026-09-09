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

import hashlib
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
CONVERSATION_SEQ_PREFIX = "sync_seq_conversation_v1"

# Narrow counters *within* the company scope.
#
# The coarse company counter above stays exactly as it was — it is the ETag input,
# so it must keep moving on every company-visible write or the digest would start
# serving stale 304s. These are additive, and exist for a different job: they are
# reported to clients so a client can tell *which* of its queries went stale.
#
# Without them every consumer shares one signal, so an inbound WhatsApp message
# marks the calls list, the arrivals board and team chat stale too, and each of
# those refetches for nothing. The digest is a change feed only if its changes are
# separable.
COMPANY_SLICE_PREFIXES = {
    "chat": "sync_seq_company_chat_v1",  # LeadWhatsAppMessage
    "calls": "sync_seq_company_calls_v1",  # WhatsAppCall
    "arrivals": "sync_seq_company_arrivals_v1",  # LeadArrival + notified_users
    "tenant_chat": "sync_seq_company_tchat_v1",  # ChatMessage
    "inbox": "sync_seq_company_inbox_v1",  # SocialMessage + SocialConversation
}


def company_seq_key(company_id: int) -> str:
    return f"{COMPANY_SEQ_PREFIX}:{company_id}"


def company_slice_key(slice_name: str, company_id: int) -> str:
    return f"{COMPANY_SLICE_PREFIXES[slice_name]}:{company_id}"


def user_seq_key(user_id: int) -> str:
    return f"{USER_SEQ_PREFIX}:{user_id}"


def conversation_seq_key(conversation_id: int) -> str:
    return f"{CONVERSATION_SEQ_PREFIX}:{conversation_id}"


def normalize_etag(raw: str) -> str:
    """Strip the weak marker and quotes so a client's If-None-Match compares cleanly."""
    token = (raw or "").strip()
    if token.startswith("W/"):
        token = token[2:].strip()
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        token = token[1:-1]
    return token


# Listeners notified whenever a counter moves.
#
# This exists so the realtime channel cannot drift out of step with the counters.
# Publishing from the signal receivers instead would cover only the writes that go
# through model signals — not sync.cache.invalidate_badges, not the explicit bump
# in the WhatsApp mark-read view (a bulk update, which fires no signal), and not
# whatever the next such case turns out to be. Hooking the bump itself means every
# path that records a change also announces it, by construction.
#
# Kept as a callback list rather than a direct import because the dependency runs
# the other way: realtime imports from sync, so sync must not import realtime.
_change_listeners: list = []


def register_change_listener(listener) -> None:
    """Register a callable invoked as ``listener(kind, **details)`` on every bump."""
    if listener not in _change_listeners:
        _change_listeners.append(listener)


def _notify(kind: str, **details) -> None:
    for listener in _change_listeners:
        try:
            listener(kind, **details)
        except Exception:
            # A listener must never break the write that triggered it. Realtime
            # delivery is an optimisation; the counter it accompanies is what
            # actually keeps clients correct.
            pass


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


def bump_company_slice(slice_name: str, company_id) -> None:
    """
    Bump one narrow company slice *and* the coarse company counter.

    Deliberately does both, so a caller cannot move a slice without also rotating
    the ETag. If the coarse bump were left to the caller, forgetting it would not
    fail a test — it would serve a 304 to a client whose data had in fact changed,
    which is the one bug this whole mechanism exists to prevent.
    """
    if not company_id:
        return
    _bump(company_slice_key(slice_name, company_id))
    _bump(company_seq_key(company_id))
    _notify("company_slice", slice_name=slice_name, company_id=company_id)


def bump_user(user_id) -> None:
    """Something changed that only this user sees."""
    if user_id:
        _bump(user_seq_key(user_id))
        _notify("user", user_id=user_id)


def bump_conversation(conversation_id) -> None:
    """A chat thread's contents or read receipts changed."""
    if conversation_id:
        _bump(conversation_seq_key(conversation_id))
        # Notified like the other scopes so a read cursor moving reaches the open
        # thread. Without it the only company-wide counter for team chat is the
        # one ChatMessage bumps, which is why a "seen" tick used to wait for the
        # next message to arrive.
        _notify("conversation", conversation_id=conversation_id)


def conversation_token(conversation_id, user_id, variant: str = "") -> str:
    """
    Version token for one chat thread as seen by one user.

    ``variant`` must capture every request parameter that changes the response
    (ordering, paging, anchors) — otherwise a client that scrolls would be handed
    a 304 for a page it has never seen. It is hashed rather than embedded so the
    header stays short regardless of how many params are involved.

    The user id is part of the token because the payload is not the same for
    everyone: read receipts are computed against the *other* participant's cursor.
    """
    seq = cache.get(conversation_seq_key(conversation_id)) or 0
    bucket = int(time.time() // SAFETY_BUCKET_SECONDS)
    digest = hashlib.md5(variant.encode("utf-8")).hexdigest()[:8] if variant else "0"
    return f"{conversation_id}.{user_id}.{seq}.{digest}.{bucket}"


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


def slice_versions(user) -> dict:
    """
    Per-slice counters for this user, as reported in the digest body.

    This is what turns the digest from a set of counts into a change feed: a client
    holds the last values it saw and refetches only the queries whose slice moved,
    instead of running a timer per query.

    One ``get_many`` and no database access, same as ``digest_token``. It is only
    called on the 200 path — a 304 never needs it, because by definition nothing
    moved.

    Missing counters read as 0, which is correct on a cold cache: a client's first
    digest establishes the baseline, and the first real write moves it to 1.
    """
    company_id = getattr(user, "company_id", None)

    keys = {"global": GLOBAL_SEQ_KEY, "user": user_seq_key(user.id)}
    if company_id:
        keys["company"] = company_seq_key(company_id)
        for name in COMPANY_SLICE_PREFIXES:
            keys[name] = company_slice_key(name, company_id)

    values = cache.get_many(list(keys.values()))
    return {name: int(values.get(key) or 0) for name, key in keys.items()}
