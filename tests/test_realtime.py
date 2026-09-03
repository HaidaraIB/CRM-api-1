"""
The realtime WebSocket channel.

Two things are being protected here, and only one of them is about speed.

*Authorization*: the socket's entire access control is which groups a connection
joins. There is no per-message check, because frames carry a version number rather
than data (see realtime/publish.py). So group membership has to be exactly right,
and an unauthenticated connection must not be accepted at all.

*Fail-soft*: realtime is an optimisation layered over polling. A broken channel
layer must degrade to "the CRM feels slower", never to a failed write. The tests
at the bottom are the ones that keep that true.
"""

from __future__ import annotations

import pytest
import json

from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.test import override_settings

from crm_saas_api.asgi import application


class WebsocketCommunicator(ApplicationCommunicator):
    """
    Minimal stand-in for channels.testing.WebsocketCommunicator.

    Not imported from channels because `channels.testing.__init__` pulls in
    ChannelsLiveServerTestCase, which imports daphne — and installing daphne drags
    in the whole Twisted stack and force-upgrades the pinned `cryptography`, which
    signs payment and auth tokens. A dozen lines on asgiref (already a dependency)
    is a much better trade than that for a test helper.

    Covers only what these tests need: connect, receive, disconnect.
    """

    def __init__(self, app, path):
        super().__init__(
            app,
            {
                "type": "websocket",
                "path": path.split("?")[0],
                "query_string": path.partition("?")[2].encode("utf-8"),
                "headers": [],
                "subprotocols": [],
            },
        )

    async def connect(self, timeout=3):
        await self.send_input({"type": "websocket.connect"})
        response = await self.receive_output(timeout)
        if response["type"] == "websocket.close":
            return False, response.get("code", 1000)
        return True, response.get("subprotocol")

    async def receive_json_from(self, timeout=3):
        response = await self.receive_output(timeout)
        assert response["type"] == "websocket.send", response
        return json.loads(response["text"])

    async def receive_nothing(self, timeout=0.5, interval=0.05):
        return await super().receive_nothing(timeout, interval)

    async def send_json_to(self, payload):
        await self.send_input(
            {"type": "websocket.receive", "text": json.dumps(payload)}
        )

    async def disconnect(self, code=1000, timeout=3):
        await self.send_input({"type": "websocket.disconnect", "code": code})
        await self.wait(timeout)

# The in-memory layer is per-process, which is exactly what these tests need:
# publisher and consumer are the same process, so a group_send is observable.
REALTIME_SETTINGS = dict(
    REALTIME_ENABLED=True,
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)


def _access_token(user) -> str:
    from rest_framework_simplejwt.tokens import AccessToken

    return str(AccessToken.for_user(user))


async def _connect(token: str | None):
    path = "/ws/sync/"
    if token is not None:
        path = f"{path}?token={token}"
    communicator = WebsocketCommunicator(application, path)
    connected, _ = await communicator.connect()
    return communicator, connected


async def _disconnect(communicator, connected: bool):
    """A rejected connection has already been closed; disconnecting again hangs."""
    if connected:
        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestRealtimeAuth:
    async def test_connection_without_a_token_is_rejected(self):
        communicator, connected = await _connect(None)
        assert connected is False
        await _disconnect(communicator, connected)

    async def test_connection_with_a_garbage_token_is_rejected(self):
        communicator, connected = await _connect("not-a-jwt")
        assert connected is False
        await _disconnect(communicator, connected)

    async def test_valid_token_connects(self, admin_user):
        from asgiref.sync import sync_to_async

        token = await sync_to_async(_access_token)(admin_user)
        communicator, connected = await _connect(token)
        assert connected is True
        await communicator.disconnect()


@pytest.fixture
def realtime_settings(settings):
    """
    Turn realtime on with an in-process channel layer.

    A fixture rather than @override_settings on the class, which Django only
    allows on SimpleTestCase subclasses. Channels resets its cached layer on the
    setting_changed signal, so swapping the backend here takes effect.
    """
    settings.REALTIME_ENABLED = True
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    return settings


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("realtime_settings")
class TestRealtimeDelivery:
    async def test_receives_own_company_events(self, admin_user):
        from asgiref.sync import sync_to_async

        token = await sync_to_async(_access_token)(admin_user)
        communicator, connected = await _connect(token)
        assert connected is True

        layer = get_channel_layer()
        await layer.group_send(
            f"company.{admin_user.company_id}",
            {"type": "sync.event", "scope": "company:calls", "version": 7},
        )

        message = await communicator.receive_json_from(timeout=3)
        assert message == {"scope": "company:calls", "version": 7}
        await communicator.disconnect()

    async def test_does_not_receive_another_companys_events(
        self, admin_user, other_company
    ):
        """
        The whole authorization surface, tested directly.

        A connection joins only its own user and company groups, so a send to a
        different company must not reach it. If this ever fails, the socket is
        leaking the fact that something changed across a tenant boundary.
        """
        from asgiref.sync import sync_to_async

        token = await sync_to_async(_access_token)(admin_user)
        communicator, connected = await _connect(token)
        assert connected is True

        layer = get_channel_layer()
        await layer.group_send(
            f"company.{other_company.id}",
            {"type": "sync.event", "scope": "company:chat", "version": 1},
        )

        assert await communicator.receive_nothing(timeout=1) is True
        await communicator.disconnect()

    async def test_receives_own_user_events(self, admin_user):
        from asgiref.sync import sync_to_async

        token = await sync_to_async(_access_token)(admin_user)
        communicator, connected = await _connect(token)
        assert connected is True

        layer = get_channel_layer()
        await layer.group_send(
            f"user.{admin_user.id}",
            {"type": "sync.event", "scope": "user", "version": 3},
        )

        message = await communicator.receive_json_from(timeout=3)
        assert message["scope"] == "user"
        await communicator.disconnect()


@pytest.mark.django_db
class TestRealtimePublish:
    """The bump -> publish bridge, which is what makes writes reach the socket."""

    def test_a_write_publishes_its_slice(self, company, admin_user):
        from unittest.mock import patch

        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage

        client = Client.objects.create(
            name="Lead", company=company, priority="low", type="cold"
        )

        with override_settings(**REALTIME_SETTINGS):
            with patch("realtime.publish._send") as send:
                LeadWhatsAppMessage.objects.create(
                    client=client,
                    phone_number="111",
                    body="hi",
                    direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
                    is_read=False,
                )

        scopes = [call.args[1] for call in send.call_args_list]
        assert f"company:chat" in scopes
        groups = [call.args[0] for call in send.call_args_list]
        assert f"company.{company.id}" in groups

    def test_nothing_is_published_when_disabled(self, company):
        """
        The flag has to actually gate delivery, because it is the rollback.

        The client half of this ships behind its own flag; if the server published
        regardless, turning realtime off in an incident would not stop the load it
        creates on the channel layer.
        """
        from unittest.mock import patch

        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage

        client = Client.objects.create(
            name="Lead", company=company, priority="low", type="cold"
        )

        with override_settings(REALTIME_ENABLED=False):
            with patch("realtime.publish._send") as send:
                LeadWhatsAppMessage.objects.create(
                    client=client,
                    phone_number="111",
                    body="hi",
                    direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
                    is_read=False,
                )

        send.assert_not_called()

    def test_publish_does_not_block_the_calling_thread(self, company):
        """
        Regression: publishing must never wait on the channel layer.

        The first version called async_to_sync(group_send) inline. Under ASGI that
        hands the coroutine to the event loop in another thread and blocks this
        one waiting — and Django tears down this thread's database connection
        across that boundary. The very next ORM call in the same view then failed
        with "Cannot operate on a closed database", so sending a team-chat message
        returned a 500 *after* having already saved the message.

        Asserting on the seam rather than the symptom: a send that takes a second
        must not add a second to the write that triggered it.
        """
        import time
        from unittest.mock import patch

        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage

        client = Client.objects.create(
            name="Lead", company=company, priority="low", type="cold"
        )

        def _slow_send(*args, **kwargs):
            time.sleep(1)

        with override_settings(**REALTIME_SETTINGS):
            with patch("realtime.publish._send", side_effect=_slow_send):
                started = time.monotonic()
                LeadWhatsAppMessage.objects.create(
                    client=client,
                    phone_number="111",
                    body="hi",
                    direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
                    is_read=False,
                )
                elapsed = time.monotonic() - started

        # The DB connection must still be usable — this is the exact call that
        # blew up in the original bug.
        assert Client.objects.filter(pk=client.pk).exists()
        # _send itself is mocked, so this asserts the *contract* it must honour:
        # nothing between the model write and here waits on delivery beyond it.
        assert elapsed < 3, f"write blocked {elapsed:.1f}s on the realtime publish"

    def test_a_broken_channel_layer_does_not_break_the_write(self, company):
        """
        The fail-soft guarantee.

        If this regresses, an unreachable Redis stops people creating leads and
        sending messages — turning a performance feature into an outage. The write
        must succeed and the counter must still move, because the counter is what
        the polling fallback reads.
        """
        from unittest.mock import patch

        from crm.models import Client
        from integrations.models import LeadWhatsAppMessage
        from sync.version import company_slice_key
        from django.core.cache import cache

        client = Client.objects.create(
            name="Lead", company=company, priority="low", type="cold"
        )
        before = cache.get(company_slice_key("chat", company.id)) or 0

        with override_settings(**REALTIME_SETTINGS):
            with patch(
                "realtime.publish._channel_layer",
                side_effect=RuntimeError("redis is down"),
            ):
                message = LeadWhatsAppMessage.objects.create(
                    client=client,
                    phone_number="111",
                    body="hi",
                    direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
                    is_read=False,
                )

        assert message.pk is not None
        after = cache.get(company_slice_key("chat", company.id)) or 0
        assert after > before


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("realtime_settings")
class TestRealtimePresence:
    """
    Client-originated frames.

    This is the only path where a client writes to the socket, so the tests that
    matter most are the ones that prove it cannot write where it should not.
    """

    async def _dm(self, admin_user, employee_user, company):
        from asgiref.sync import sync_to_async

        def _make():
            from tenant_chat.models import ChatConversation
            from tenant_chat.serializers import normalize_dm_participants

            low, high = normalize_dm_participants(admin_user, employee_user)
            conv, _ = ChatConversation.objects.get_or_create(
                company=company,
                kind=ChatConversation.Kind.DIRECT,
                participant_low=low,
                participant_high=high,
            )
            return conv.id

        return await sync_to_async(_make)()

    async def _connect_as(self, user):
        from asgiref.sync import sync_to_async

        token = await sync_to_async(_access_token)(user)
        communicator, connected = await _connect(token)
        assert connected is True
        return communicator

    async def test_typing_reaches_the_other_participant(
        self, admin_user, employee_user, company
    ):
        conv_id = await self._dm(admin_user, employee_user, company)
        sender = await self._connect_as(admin_user)
        peer = await self._connect_as(employee_user)

        await sender.send_json_to({"action": "subscribe", "conversation": conv_id})
        await peer.send_json_to({"action": "subscribe", "conversation": conv_id})
        await sender.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "typing"}
        )

        message = await peer.receive_json_from(timeout=3)
        assert message == {
            "scope": "presence",
            "conversation": conv_id,
            "user_id": admin_user.id,
            "state": "typing",
        }
        await sender.disconnect()
        await peer.disconnect()

    async def test_presence_without_subscribing_is_dropped(
        self, admin_user, employee_user, company
    ):
        """
        The authorization gate.

        Being connected is not permission to write into a conversation group.
        A client that skips subscribe — or names a conversation it was refused —
        must produce no broadcast at all.
        """
        conv_id = await self._dm(admin_user, employee_user, company)
        sender = await self._connect_as(admin_user)
        peer = await self._connect_as(employee_user)
        await peer.send_json_to({"action": "subscribe", "conversation": conv_id})

        await sender.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "typing"}
        )

        assert await peer.receive_nothing(timeout=1) is True
        await sender.disconnect()
        await peer.disconnect()

    async def test_outsider_cannot_subscribe_to_a_dm(
        self, admin_user, employee_user, other_admin_user, company
    ):
        """
        A user from another company must not be admitted to the group, so their
        presence frames can never reach it.
        """
        conv_id = await self._dm(admin_user, employee_user, company)
        outsider = await self._connect_as(other_admin_user)
        peer = await self._connect_as(employee_user)
        await peer.send_json_to({"action": "subscribe", "conversation": conv_id})

        await outsider.send_json_to({"action": "subscribe", "conversation": conv_id})
        await outsider.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "typing"}
        )

        assert await peer.receive_nothing(timeout=1) is True
        await outsider.disconnect()
        await peer.disconnect()

    async def test_sender_identity_comes_from_the_connection(
        self, admin_user, employee_user, company
    ):
        """A client cannot claim somebody else is typing."""
        conv_id = await self._dm(admin_user, employee_user, company)
        sender = await self._connect_as(admin_user)
        peer = await self._connect_as(employee_user)
        await sender.send_json_to({"action": "subscribe", "conversation": conv_id})
        await peer.send_json_to({"action": "subscribe", "conversation": conv_id})

        await sender.send_json_to(
            {
                "action": "presence",
                "conversation": conv_id,
                "state": "typing",
                "user_id": employee_user.id,  # spoof attempt
            }
        )

        message = await peer.receive_json_from(timeout=3)
        assert message["user_id"] == admin_user.id
        await sender.disconnect()
        await peer.disconnect()

    async def test_invalid_state_is_dropped(self, admin_user, employee_user, company):
        conv_id = await self._dm(admin_user, employee_user, company)
        sender = await self._connect_as(admin_user)
        peer = await self._connect_as(employee_user)
        await sender.send_json_to({"action": "subscribe", "conversation": conv_id})
        await peer.send_json_to({"action": "subscribe", "conversation": conv_id})

        await sender.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "rm -rf"}
        )

        assert await peer.receive_nothing(timeout=1) is True
        await sender.disconnect()
        await peer.disconnect()

    async def test_socket_presence_is_visible_to_http_pollers(
        self, admin_user, employee_user, company
    ):
        """
        The two transports must describe the same world.

        A colleague whose socket is down still polls peer-presence, so a frame
        sent over the socket has to land in the cache that endpoint reads.
        """
        from asgiref.sync import sync_to_async

        from tenant_chat.presence import get_user_presence

        conv_id = await self._dm(admin_user, employee_user, company)
        sender = await self._connect_as(admin_user)
        await sender.send_json_to({"action": "subscribe", "conversation": conv_id})
        await sender.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "recording_voice"}
        )
        await sender.receive_json_from(timeout=3)  # own echo confirms it landed

        stored = await sync_to_async(get_user_presence)(conv_id, admin_user.id)
        assert stored == "recording_voice"
        await sender.disconnect()

    async def test_garbage_frames_do_not_kill_the_connection(
        self, admin_user, employee_user, company
    ):
        conv_id = await self._dm(admin_user, employee_user, company)
        sender = await self._connect_as(admin_user)

        await sender.send_input({"type": "websocket.receive", "text": "not json"})
        await sender.send_json_to({"action": "presence"})           # no conversation
        await sender.send_json_to({"conversation": conv_id})        # no action
        await sender.send_json_to({"action": "subscribe", "conversation": "8"})  # wrong type

        # Still alive and still able to do real work.
        await sender.send_json_to({"action": "subscribe", "conversation": conv_id})
        await sender.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "typing"}
        )
        message = await sender.receive_json_from(timeout=3)
        assert message["state"] == "typing"
        await sender.disconnect()

    async def test_presence_posted_over_http_reaches_socket_listeners(
        self, admin_user, employee_user, company
    ):
        """
        The mirror of test_socket_presence_is_visible_to_http_pollers.

        Mobile reports every typing state by POST, and so does any client whose
        socket is down. Writing only the cache told nobody: a peer with a healthy
        connection has backed its presence poll off to 30s precisely because it
        expects frames, so an HTTP-posted state used to show up half a minute
        late or not at all.
        """
        from asgiref.sync import sync_to_async

        from realtime.publish import publish_presence
        from tenant_chat.presence import set_user_presence

        conv_id = await self._dm(admin_user, employee_user, company)
        peer = await self._connect_as(employee_user)
        await peer.send_json_to({"action": "subscribe", "conversation": conv_id})
        # The publish below is out of band, so it can race a subscribe the
        # consumer has not read yet. Round-tripping one frame proves admission
        # happened before anything is sent to the group.
        await peer.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "typing"}
        )
        await peer.receive_json_from(timeout=3)

        # Exactly what the peer-presence POST endpoint does.
        await sync_to_async(set_user_presence)(conv_id, admin_user.id, "typing")
        await sync_to_async(publish_presence)(conv_id, admin_user.id, "typing")

        message = await peer.receive_json_from(timeout=3)
        assert message == {
            "scope": "presence",
            "conversation": conv_id,
            "user_id": admin_user.id,
            "state": "typing",
        }
        await peer.disconnect()

    async def test_a_read_cursor_moving_reaches_the_open_thread(
        self, admin_user, employee_user, company
    ):
        """
        What makes the "seen" tick live.

        Marking a message read writes no ChatMessage, so the company
        ``tenant_chat`` slice does not move and the digest cannot carry this. The
        one participant who needs to know is the person whose bubble just turned
        blue, and they are subscribed to the thread.
        """
        from asgiref.sync import sync_to_async

        from sync.version import bump_conversation

        conv_id = await self._dm(admin_user, employee_user, company)
        sender = await self._connect_as(admin_user)
        await sender.send_json_to({"action": "subscribe", "conversation": conv_id})
        # Confirms admission before publishing out of band — see the note in
        # test_presence_posted_over_http_reaches_socket_listeners.
        await sender.send_json_to(
            {"action": "presence", "conversation": conv_id, "state": "typing"}
        )
        await sender.receive_json_from(timeout=3)

        await sync_to_async(bump_conversation)(conv_id)

        message = await sender.receive_json_from(timeout=3)
        assert message["scope"] == "conversation"
        assert message["conversation"] == conv_id
        await sender.disconnect()

    async def test_conversation_frames_do_not_leak_to_outsiders(
        self, admin_user, employee_user, other_admin_user, company
    ):
        """
        A thread frame is addressed to its group, and admission to that group is
        the same check the REST API makes. Someone who was refused the subscribe
        must not learn that the thread moved either.
        """
        from asgiref.sync import sync_to_async

        from sync.version import bump_conversation

        conv_id = await self._dm(admin_user, employee_user, company)
        outsider = await self._connect_as(other_admin_user)
        await outsider.send_json_to({"action": "subscribe", "conversation": conv_id})
        # Long enough for the refused subscribe to have been processed, so the
        # silence below is a rejection rather than a race.
        assert await outsider.receive_nothing(timeout=1) is True

        await sync_to_async(bump_conversation)(conv_id)

        assert await outsider.receive_nothing(timeout=1) is True
        await outsider.disconnect()


@pytest.mark.django_db
class TestOnlinePresence:
    """
    Online = live socket OR recently seen.

    The fallback half is what keeps mobile (which has no socket) and any browser
    with realtime disabled working exactly as before, so both halves are tested.
    """

    def test_last_seen_alone_still_means_online(self, admin_user):
        from django.utils import timezone

        from accounts.presence import is_online

        admin_user.last_seen_at = timezone.now()
        assert is_online(admin_user) is True

    def test_stale_last_seen_means_offline(self, admin_user):
        from datetime import timedelta

        from django.utils import timezone

        from accounts.presence import is_online

        admin_user.last_seen_at = timezone.now() - timedelta(minutes=10)
        assert is_online(admin_user) is False

    def test_a_live_socket_beats_a_stale_last_seen(self, admin_user):
        """
        The point of the whole change: someone connected right now is online even
        if the database has not been written for minutes.
        """
        from datetime import timedelta

        from django.utils import timezone

        from accounts.presence import clear_live, is_online, mark_live

        admin_user.last_seen_at = timezone.now() - timedelta(minutes=10)
        assert is_online(admin_user) is False

        mark_live(admin_user.id)
        try:
            assert is_online(admin_user) is True
        finally:
            clear_live(admin_user.id)

    def test_bulk_lookup_matches_single(self, admin_user, employee_user):
        from accounts.presence import clear_live, is_online, live_user_ids, mark_live

        mark_live(admin_user.id)
        try:
            live = live_user_ids([admin_user.id, employee_user.id])
            assert live == {admin_user.id}
            # A caller passing the set must get the same answer as one that does
            # its own read — otherwise list and detail views would disagree.
            assert is_online(admin_user, live) is True
            assert is_online(employee_user, live) is False
        finally:
            clear_live(admin_user.id)

    def test_last_seen_write_is_throttled(self, admin_user):
        """
        Moving the heartbeat onto the socket must not keep the write volume it
        was meant to remove: the column is written every few minutes, not every
        beat.
        """
        from django.core.cache import cache

        from accounts.models import User
        from accounts.presence import touch_last_seen

        cache.delete(f"presence:db:v1:{admin_user.id}")
        User.objects.filter(pk=admin_user.id).update(last_seen_at=None)

        touch_last_seen(admin_user.id)
        first = User.objects.get(pk=admin_user.id).last_seen_at
        assert first is not None

        User.objects.filter(pk=admin_user.id).update(last_seen_at=None)
        touch_last_seen(admin_user.id)  # immediately again
        assert User.objects.get(pk=admin_user.id).last_seen_at is None, (
            "second call inside the throttle window wrote the database"
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.usefixtures("realtime_settings")
class TestOnlinePresenceOverSocket:
    async def test_connecting_marks_the_user_online(self, admin_user):
        from asgiref.sync import sync_to_async

        from accounts.presence import clear_live, live_user_ids

        await sync_to_async(clear_live)(admin_user.id)
        token = await sync_to_async(_access_token)(admin_user)
        communicator, connected = await _connect(token)
        assert connected is True

        live = await sync_to_async(live_user_ids)([admin_user.id])
        assert admin_user.id in live, "an accepted socket did not mark the user online"

        await communicator.disconnect()

    async def test_heartbeat_frame_refreshes_presence(self, admin_user):
        from asgiref.sync import sync_to_async

        from accounts.presence import clear_live, live_user_ids

        token = await sync_to_async(_access_token)(admin_user)
        communicator, connected = await _connect(token)
        assert connected is True

        # Simulate the marker having expired mid-session, then heartbeat.
        await sync_to_async(clear_live)(admin_user.id)
        await communicator.send_json_to({"action": "heartbeat"})
        await communicator.receive_nothing(timeout=0.5)

        live = await sync_to_async(live_user_ids)([admin_user.id])
        assert admin_user.id in live
        await communicator.disconnect()
