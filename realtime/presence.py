"""
Authorization and fan-out for client-originated socket frames.

Everything else on this channel flows server → client and carries no data, so a
connection's group membership was the entire security surface. Presence reverses
that: a client now *sends*, and whatever it sends is broadcast to other people.

Two rules follow, and both are enforced here rather than in the consumer so they
cannot be forgotten at a call site:

1. **A connection may only address a conversation it has been admitted to.**
   Admission is checked once, against the same predicate the REST API uses
   (`user_participates_in_conversation`), and the resulting id is remembered on
   the connection. A frame naming any other conversation is dropped — being
   connected is not permission to write into an arbitrary group.

2. **The sender's identity comes from the connection, never from the frame.**
   The payload's `user_id` is filled in from the authenticated scope, so a client
   cannot claim to be someone else typing.
"""

from __future__ import annotations

import time

from channels.db import database_sync_to_async

# Re-exported from publish.py, which owns the group/event vocabulary because it
# is the half of realtime that must stay importable without ``channels``. Two
# definitions of "conversation.{}" is one too many: the consumer joins the group
# the publisher sends to, and they have to agree.
from .publish import CONVERSATION_GROUP, PRESENCE_EVENT_TYPE as PRESENCE_EVENT  # noqa: F401

# A presence frame is a keystroke-rate signal, so it needs a ceiling. This allows
# comfortably more than the ~1 frame every 3s a well-behaved client sends, while
# stopping a loop from turning one socket into a broadcast amplifier.
RATE_LIMIT_FRAMES = 40
RATE_LIMIT_WINDOW_SECONDS = 10


class RateLimiter:
    """Fixed-window counter, one per connection."""

    def __init__(self, limit: int = RATE_LIMIT_FRAMES, window: int = RATE_LIMIT_WINDOW_SECONDS):
        self.limit = limit
        self.window = window
        self._count = 0
        self._window_start = 0.0

    def allow(self) -> bool:
        now = time.monotonic()
        if now - self._window_start > self.window:
            self._window_start = now
            self._count = 0
        self._count += 1
        return self._count <= self.limit


@database_sync_to_async
def user_may_join_conversation(user_id: int, conversation_id: int) -> bool:
    """
    Re-checked against the database, not against anything the client asserts.

    Deliberately the same predicate the REST endpoints use — if the two ever
    disagreed, the socket would become a way to observe a conversation the API
    refuses to show.
    """
    from django.contrib.auth import get_user_model

    from tenant_chat.authorization import user_participates_in_conversation
    from tenant_chat.models import ChatConversation

    User = get_user_model()
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return False
    conversation = ChatConversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        return False
    return user_participates_in_conversation(user, conversation)


@database_sync_to_async
def record_presence(conversation_id: int, user_id: int, state: str) -> None:
    """
    Mirror socket presence into the cache the HTTP endpoint reads.

    Without this, a tab on the socket would appear idle to a colleague whose
    socket is down and who is therefore still polling `peer-presence` — the two
    transports have to describe the same world.
    """
    from tenant_chat.presence import set_user_presence

    set_user_presence(conversation_id, user_id, state)


def is_valid_state(state: str) -> bool:
    from tenant_chat.presence import VALID_ACTIONS

    return state in VALID_ACTIONS
