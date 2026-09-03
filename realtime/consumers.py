"""
The one WebSocket consumer.

It does almost nothing on purpose. A connection joins two groups — the user's own
and their company's — and forwards whatever arrives to the socket. There is no
per-message authorization because there is nothing in a message to authorize: see
the note in realtime/publish.py on why frames carry a version number and not data.

Inbound frames are accepted for one thing only: chat presence (typing, recording,
uploading). That is the case a socket is actually better at — an ephemeral signal
that never touches the database and whose value is entirely in arriving now.

Because clients can now write, the receive path has its own authorization, kept in
realtime/presence.py: a connection may only address a conversation it has been
admitted to, admission is re-checked against the same predicate the REST API uses,
and the sender's identity is taken from the connection rather than the frame.
"""

from __future__ import annotations

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .auth import authenticate_scope
from .online import go_live, go_offline
from .presence import (
    CONVERSATION_GROUP,
    PRESENCE_EVENT,
    RateLimiter,
    is_valid_state,
    record_presence,
    user_may_join_conversation,
)
from .publish import COMPANY_GROUP, USER_GROUP

logger = logging.getLogger(__name__)

# Close codes. 4401 mirrors HTTP 401 so the client can tell "your token is bad,
# refresh it" from an ordinary network drop and avoid a hot reconnect loop.
CLOSE_UNAUTHORIZED = 4401


class SyncConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = await database_sync_to_async(authenticate_scope)(self.scope)
        if user is None:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return

        self.user_id = user.id
        self.group_names = [USER_GROUP.format(user.id)]
        company_id = getattr(user, "company_id", None)
        if company_id:
            self.group_names.append(COMPANY_GROUP.format(company_id))

        # Conversations this connection has been admitted to. Empty until the
        # client subscribes and the server verifies access — this set, not the
        # client's claim, is what a presence frame is checked against.
        self.conversations: set[int] = set()
        self.rate_limiter = RateLimiter()

        for group in self.group_names:
            await self.channel_layer.group_add(group, self.channel_name)

        # An open socket is the strongest evidence of presence there is, so the
        # green dot lights the moment we accept rather than up to a heartbeat
        # later.
        await go_live(user.id)

        await self.accept()

    async def disconnect(self, code):
        if getattr(self, "user_id", None):
            await go_offline(self.user_id)
        for group in getattr(self, "group_names", []):
            try:
                await self.channel_layer.group_discard(group, self.channel_name)
            except Exception:  # pragma: no cover - layer already gone
                pass
        for conversation_id in getattr(self, "conversations", set()):
            try:
                await self.channel_layer.group_discard(
                    CONVERSATION_GROUP.format(conversation_id), self.channel_name
                )
            except Exception:  # pragma: no cover
                pass

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle a client frame.

        Every failure path is a silent drop rather than an error reply. A client
        that guesses a conversation id learns nothing from the response either
        way, and an unauthenticated connection never reaches here at all.
        """
        if not getattr(self, "user_id", None):
            return
        if not self.rate_limiter.allow():
            return

        try:
            payload = json.loads(text_data or "{}")
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return

        action = payload.get("action")

        # Handled before the conversation check below: a heartbeat is about the
        # user, not a thread, and carries no conversation id.
        if action == "heartbeat":
            await go_live(self.user_id)
            return

        conversation_id = payload.get("conversation")
        if not isinstance(conversation_id, int) or conversation_id <= 0:
            return

        if action == "subscribe":
            await self._subscribe(conversation_id)
        elif action == "unsubscribe":
            await self._unsubscribe(conversation_id)
        elif action == "presence":
            await self._presence(conversation_id, payload.get("state"))

    async def _subscribe(self, conversation_id: int) -> None:
        if conversation_id in self.conversations:
            return
        if not await user_may_join_conversation(self.user_id, conversation_id):
            return
        self.conversations.add(conversation_id)
        await self.channel_layer.group_add(
            CONVERSATION_GROUP.format(conversation_id), self.channel_name
        )

    async def _unsubscribe(self, conversation_id: int) -> None:
        if conversation_id not in self.conversations:
            return
        self.conversations.discard(conversation_id)
        await self.channel_layer.group_discard(
            CONVERSATION_GROUP.format(conversation_id), self.channel_name
        )

    async def _presence(self, conversation_id: int, state) -> None:
        # The authorization gate: only a conversation this connection was
        # admitted to during subscribe. A frame naming any other id is dropped,
        # so being connected is not permission to write into an arbitrary group.
        if conversation_id not in self.conversations:
            return
        if not isinstance(state, str) or not is_valid_state(state):
            return

        # Keep the cache the HTTP endpoint reads in step, so a colleague whose
        # socket is down still sees this user as active.
        await record_presence(conversation_id, self.user_id, state)

        await self.channel_layer.group_send(
            CONVERSATION_GROUP.format(conversation_id),
            {
                "type": PRESENCE_EVENT,
                "conversation": conversation_id,
                # From the connection, never from the frame — a client cannot
                # claim to be somebody else typing.
                "user_id": self.user_id,
                "state": state,
            },
        )

    async def presence_event(self, event):
        """Handler for the ``presence.event`` type sent by _presence above."""
        # Echoed to the sender too; the client filters on user_id. Suppressing it
        # here would mean a second tab of the same user never saw its own state.
        await self.send(
            text_data=json.dumps(
                {
                    "scope": "presence",
                    "conversation": event.get("conversation"),
                    "user_id": event.get("user_id"),
                    "state": event.get("state"),
                }
            )
        )

    async def conversation_event(self, event):
        """
        Handler for the ``conversation.event`` type sent by realtime/publish.py.

        Like every other server frame it carries a version and no content: the
        client re-reads the thread through the REST endpoint, which already
        filters by participation and answers 304 when nothing actually moved.
        """
        await self.send(
            text_data=json.dumps(
                {
                    "scope": "conversation",
                    "conversation": event.get("conversation"),
                    "version": event.get("version"),
                }
            )
        )

    async def sync_event(self, event):
        """Handler for the ``sync.event`` type sent by realtime/publish.py."""
        await self.send(
            text_data=json.dumps(
                {"scope": event.get("scope"), "version": event.get("version")}
            )
        )
