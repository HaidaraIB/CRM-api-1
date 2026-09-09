"""
Phase 2 — Meta Inbox ingestion semantics.

Tenant resolution, contact/conversation creation, dedupe, echo handling,
reactions/postbacks/deliveries, media, and the plan/policy gate.

The load-bearing assertion in this file is that ingestion creates NO crm.Client:
conversations stay lead-less until an agent converts one.
"""

import pytest
from django.utils import timezone

from integrations.services.meta_inbox_ingest import process_entry

pytestmark = pytest.mark.django_db

IGSID = "4900000000000001"
PSID = "5900000000000001"


def ig_entry(connection, *, mid="mid-1", text="hello", timestamp=None, sender=None, **message_extra):
    message = {"mid": mid, "text": text}
    message.update(message_extra)
    return {
        "id": connection.ig_user_id,
        "time": 1700000000000,
        "messaging": [
            {
                "sender": {"id": sender or IGSID},
                "recipient": {"id": connection.ig_user_id},
                "timestamp": timestamp or 1700000000000,
                "message": message,
            }
        ],
    }


def page_entry(connection, *, mid="mid-p1", text="hi there"):
    return {
        "id": connection.page_id,
        "messaging": [
            {
                "sender": {"id": PSID},
                "recipient": {"id": connection.page_id},
                "timestamp": 1700000000000,
                "message": {"mid": mid, "text": text},
            }
        ],
    }


class TestTenantResolution:
    def test_instagram_entry_resolves_by_ig_user_id(self, meta_inbox_connection):
        from integrations.models import SocialConversation

        assert process_entry("instagram", ig_entry(meta_inbox_connection)) == 1
        conversation = SocialConversation.objects.get()
        assert conversation.company_id == meta_inbox_connection.company_id
        assert conversation.channel == "instagram"

    def test_page_entry_resolves_by_page_id(self, meta_inbox_connection):
        from integrations.models import SocialConversation

        assert process_entry("page", page_entry(meta_inbox_connection)) == 1
        assert SocialConversation.objects.get().channel == "messenger"

    def test_instagram_entry_falls_back_to_page_id(self, meta_inbox_connection):
        """Some IG payloads carry the Page id in entry.id."""
        from integrations.models import SocialConversation

        entry = ig_entry(meta_inbox_connection)
        entry["id"] = meta_inbox_connection.page_id
        assert process_entry("instagram", entry) == 1
        assert SocialConversation.objects.count() == 1

    def test_unknown_entry_id_creates_nothing(self, meta_inbox_connection):
        from integrations.models import SocialConversation

        entry = ig_entry(meta_inbox_connection)
        entry["id"] = "999999999999"
        assert process_entry("instagram", entry) == 0
        assert SocialConversation.objects.count() == 0

    def test_disconnected_connection_is_not_resolved(self, meta_inbox_connection):
        from integrations.models import SocialConversation

        meta_inbox_connection.status = "disconnected"
        meta_inbox_connection.save(update_fields=["status"])
        assert process_entry("instagram", ig_entry(meta_inbox_connection)) == 0
        assert SocialConversation.objects.count() == 0

    def test_cross_tenant_isolation(
        self, meta_inbox_connection, other_company_inbox_connection
    ):
        from integrations.models import SocialConversation

        process_entry("instagram", ig_entry(other_company_inbox_connection))
        conversation = SocialConversation.objects.get()
        assert conversation.company_id == other_company_inbox_connection.company_id
        assert conversation.company_id != meta_inbox_connection.company_id


class TestNoLeadIsCreated:
    def test_inbound_dm_creates_no_client(self, meta_inbox_connection):
        """
        The core product decision: DMs are lead-less until converted. Creating a
        Client per DM would burn the plan's max_clients quota on spam.
        """
        from crm.models import Client
        from integrations.models import SocialContact, SocialConversation

        process_entry("instagram", ig_entry(meta_inbox_connection))

        assert Client.objects.count() == 0
        assert SocialContact.objects.count() == 1
        assert SocialConversation.objects.get().client_id is None


class TestContactAndConversation:
    def test_second_message_reuses_contact_and_conversation(self, meta_inbox_connection):
        from integrations.models import SocialContact, SocialConversation, SocialMessage

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1"))
        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m2", text="again"))

        assert SocialContact.objects.count() == 1
        assert SocialConversation.objects.count() == 1
        assert SocialMessage.objects.count() == 2

    def test_conversation_counters_roll_forward(self, meta_inbox_connection):
        from integrations.models import SocialConversation

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1", text="first"))
        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m2", text="second"))

        conversation = SocialConversation.objects.get()
        assert conversation.unread_count == 2
        assert conversation.last_message_preview == "second"
        assert conversation.last_message_direction == "inbound"
        assert conversation.last_inbound_at is not None

    def test_meta_timestamp_is_authoritative(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        process_entry(
            "instagram", ig_entry(meta_inbox_connection, timestamp=1700000000000)
        )
        message = SocialMessage.objects.get()
        assert message.sent_at is not None
        assert message.sent_at.year == 2023

    def test_inbound_reopens_a_done_conversation(self, meta_inbox_connection):
        from integrations.models import SocialConversation, WhatsAppConversationStatus

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1"))
        SocialConversation.objects.update(status=WhatsAppConversationStatus.DONE)

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m2"))
        assert SocialConversation.objects.get().status == WhatsAppConversationStatus.OPEN

    def test_spam_status_is_sticky(self, meta_inbox_connection):
        """An agent's spam call should survive the sender pinging again."""
        from integrations.models import SocialConversation, WhatsAppConversationStatus

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1"))
        SocialConversation.objects.update(status=WhatsAppConversationStatus.SPAM)

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m2"))
        assert SocialConversation.objects.get().status == WhatsAppConversationStatus.SPAM


class TestDedupe:
    def test_same_mid_delivered_twice_stores_once(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        entry = ig_entry(meta_inbox_connection, mid="duplicate-mid")
        assert process_entry("instagram", entry) == 1
        assert process_entry("instagram", entry) == 0
        assert SocialMessage.objects.filter(external_message_id="duplicate-mid").count() == 1


class TestEcho:
    def test_echo_is_outbound_and_does_not_mark_unread(self, meta_inbox_connection):
        """The business replied from the native app; the thread is not unread."""
        from integrations.models import SocialConversation, SocialMessage

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1"))
        before = SocialConversation.objects.get().unread_count

        entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": meta_inbox_connection.ig_user_id},
                    "recipient": {"id": IGSID},
                    "timestamp": 1700000100000,
                    "message": {"mid": "echo-1", "text": "replied from phone", "is_echo": True},
                }
            ],
        }
        process_entry("instagram", entry)

        echo = SocialMessage.objects.get(external_message_id="echo-1")
        assert echo.direction == "outbound"
        assert echo.is_echo is True

        conversation = SocialConversation.objects.get()
        assert conversation.unread_count == before
        assert conversation.last_message_direction == "outbound"

    def test_echo_of_our_own_send_updates_instead_of_duplicating(
        self, meta_inbox_connection
    ):
        from integrations.models import SocialMessage

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1"))
        conversation = SocialMessage.objects.get().conversation

        ours = SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_OUTBOUND,
            external_message_id="agent-mid",
            body="from the CRM",
            delivery_status="pending",
        )

        entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": meta_inbox_connection.ig_user_id},
                    "recipient": {"id": IGSID},
                    "timestamp": 1700000200000,
                    "message": {"mid": "agent-mid", "text": "from the CRM", "is_echo": True},
                }
            ],
        }
        process_entry("instagram", entry)

        assert SocialMessage.objects.filter(external_message_id="agent-mid").count() == 1
        ours.refresh_from_db()
        assert ours.delivery_status == "sent"


class TestOtherEventTypes:
    def test_postback_stored_as_inbound_message(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": IGSID},
                    "recipient": {"id": meta_inbox_connection.ig_user_id},
                    "timestamp": 1700000000000,
                    "postback": {"mid": "pb-1", "title": "Get Started"},
                }
            ],
        }
        process_entry("instagram", entry)

        message = SocialMessage.objects.get(external_message_id="pb-1")
        assert message.direction == "inbound"
        assert message.body == "Get Started"

    def test_reaction_updates_target_and_creates_no_row(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="target-mid"))

        entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": IGSID},
                    "recipient": {"id": meta_inbox_connection.ig_user_id},
                    "reaction": {"mid": "target-mid", "action": "react", "emoji": "❤️"},
                }
            ],
        }
        process_entry("instagram", entry)

        assert SocialMessage.objects.count() == 1
        assert SocialMessage.objects.get().reaction == "❤️"

    def test_unreact_clears_the_reaction(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="target-mid"))
        SocialMessage.objects.update(reaction="❤️")

        entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": IGSID},
                    "recipient": {"id": meta_inbox_connection.ig_user_id},
                    "reaction": {"mid": "target-mid", "action": "unreact"},
                }
            ],
        }
        process_entry("instagram", entry)
        assert SocialMessage.objects.get().reaction == ""

    def test_delivery_marks_outbound_delivered(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1"))
        conversation = SocialMessage.objects.get().conversation
        SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_OUTBOUND,
            external_message_id="out-1",
            body="sent",
            delivery_status="sent",
        )

        entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": IGSID},
                    "recipient": {"id": meta_inbox_connection.ig_user_id},
                    "delivery": {"mids": ["out-1"]},
                }
            ],
        }
        process_entry("instagram", entry)

        assert SocialMessage.objects.get(external_message_id="out-1").delivery_status == "delivered"

    def test_delivery_does_not_resurrect_a_failed_send(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="m1"))
        conversation = SocialMessage.objects.get().conversation
        SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_OUTBOUND,
            external_message_id="out-fail",
            delivery_status="failed",
        )

        entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": IGSID},
                    "recipient": {"id": meta_inbox_connection.ig_user_id},
                    "delivery": {"mids": ["out-fail"]},
                }
            ],
        }
        process_entry("instagram", entry)
        assert SocialMessage.objects.get(external_message_id="out-fail").delivery_status == "failed"


class TestMedia:
    def test_image_attachment_is_downloaded(self, meta_inbox_connection, monkeypatch):
        from integrations.models import SocialMessage

        monkeypatch.setattr(
            "integrations.services.meta_inbox_media.download_attachment",
            lambda url: (b"fake-image-bytes", "image/jpeg"),
        )
        entry = ig_entry(
            meta_inbox_connection,
            mid="img-1",
            text="",
            attachments=[
                {"type": "image", "payload": {"url": "https://cdn.meta/x/photo.jpg"}}
            ],
        )
        process_entry("instagram", entry)

        message = SocialMessage.objects.get()
        assert message.attachment_kind == "image"
        assert message.attachment_size == len(b"fake-image-bytes")
        assert message.source_media_url == "https://cdn.meta/x/photo.jpg"

    def test_failed_download_keeps_message_and_url(self, meta_inbox_connection, monkeypatch):
        """The CDN URL expires in hours — keep it so a manual retry is possible."""
        from integrations.models import SocialMessage

        monkeypatch.setattr(
            "integrations.services.meta_inbox_media.download_attachment", lambda url: None
        )
        entry = ig_entry(
            meta_inbox_connection,
            mid="img-2",
            text="",
            attachments=[
                {"type": "image", "payload": {"url": "https://cdn.meta/x/gone.jpg"}}
            ],
        )
        process_entry("instagram", entry)

        message = SocialMessage.objects.get()
        assert message.attachment_kind == "image"
        assert not message.attachment
        assert message.source_media_url == "https://cdn.meta/x/gone.jpg"

    def test_story_mention_is_not_mirrored_into_storage(
        self, meta_inbox_connection, monkeypatch
    ):
        """Content the business does not own stays a URL — storage has no retention policy."""
        from integrations.models import SocialMessage

        called = []
        monkeypatch.setattr(
            "integrations.services.meta_inbox_media.download_attachment",
            lambda url: called.append(url),
        )
        entry = ig_entry(
            meta_inbox_connection,
            mid="story-1",
            text="",
            attachments=[
                {"type": "story_mention", "payload": {"url": "https://cdn.meta/story.mp4"}}
            ],
        )
        process_entry("instagram", entry)

        assert called == []
        assert SocialMessage.objects.get().attachment_kind == "story_mention"

    def test_oversized_attachment_is_skipped(self, meta_inbox_connection, monkeypatch, settings):
        from integrations.models import SocialMessage
        from integrations.services import meta_inbox_media

        settings.META_INBOX_MAX_MEDIA_BYTES = 10

        class FakeResponse:
            ok = True
            headers = {"Content-Length": "5000", "Content-Type": "image/jpeg"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_content(self, chunk_size=None):
                yield b"x" * 5000

        monkeypatch.setattr(
            meta_inbox_media.requests, "get", lambda *a, **k: FakeResponse()
        )
        entry = ig_entry(
            meta_inbox_connection,
            mid="big-1",
            text="",
            attachments=[{"type": "image", "payload": {"url": "https://cdn.meta/big.jpg"}}],
        )
        process_entry("instagram", entry)

        message = SocialMessage.objects.get()
        assert not message.attachment
        assert message.attachment_kind == "image"

    def test_location_attachment_stores_coordinates(self, meta_inbox_connection):
        from integrations.models import SocialMessage

        entry = ig_entry(
            meta_inbox_connection,
            mid="loc-1",
            text="",
            attachments=[
                {
                    "type": "location",
                    "payload": {"title": "Office", "coordinates": {"lat": 33.3, "long": 44.4}},
                }
            ],
        )
        process_entry("instagram", entry)

        message = SocialMessage.objects.get()
        assert message.attachment_kind == "location"
        assert float(message.location_latitude) == pytest.approx(33.3)
        assert message.location_name == "Office"


class TestGating:
    def test_plan_disabled_drops_the_event(self, meta_inbox_connection, monkeypatch):
        from integrations.models import SocialConversation

        monkeypatch.setattr(
            "integrations.services.meta_inbox_ingest.get_plan_integration_access",
            lambda company, platform: {"enabled": False, "message": "", "scope": "plan"},
        )
        assert process_entry("instagram", ig_entry(meta_inbox_connection)) == 0
        assert SocialConversation.objects.count() == 0

    def test_admin_policy_disabled_drops_the_event(self, meta_inbox_connection, monkeypatch):
        from integrations.models import SocialConversation

        monkeypatch.setattr(
            "integrations.services.meta_inbox_ingest.get_effective_integration_policy",
            lambda policies, company_id, platform: {
                "enabled": False,
                "message": "off",
                "scope": "global",
            },
        )
        assert process_entry("instagram", ig_entry(meta_inbox_connection)) == 0
        assert SocialConversation.objects.count() == 0


class TestSideEffects:
    def test_integration_log_written_on_success(self, meta_inbox_connection):
        from integrations.models import IntegrationLog

        process_entry("instagram", ig_entry(meta_inbox_connection))
        assert IntegrationLog.objects.filter(action="meta_inbox_message_received").exists()

    def test_last_webhook_at_is_stamped(self, meta_inbox_connection):
        from integrations.models import MetaInboxConnection

        process_entry("instagram", ig_entry(meta_inbox_connection))
        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.last_webhook_at is not None

    def test_inbound_bumps_the_inbox_sync_slice(self, meta_inbox_connection):
        """Without this the conversation list would 304 with stale rows."""
        from django.core.cache import cache
        from sync.version import company_slice_key

        key = company_slice_key("inbox", meta_inbox_connection.company_id)
        before = cache.get(key)
        process_entry("instagram", ig_entry(meta_inbox_connection))
        assert cache.get(key) != before

    def test_inbox_slice_is_registered(self):
        from sync.version import COMPANY_SLICE_PREFIXES

        assert "inbox" in COMPANY_SLICE_PREFIXES

    def test_push_is_sent_for_inbound_only(self, meta_inbox_connection, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "integrations.services.social_push.notify_social_inbound",
            lambda **kwargs: sent.append(kwargs),
        )

        process_entry("instagram", ig_entry(meta_inbox_connection, mid="in-1"))
        assert len(sent) == 1

        echo_entry = {
            "id": meta_inbox_connection.ig_user_id,
            "messaging": [
                {
                    "sender": {"id": meta_inbox_connection.ig_user_id},
                    "recipient": {"id": IGSID},
                    "timestamp": 1700000300000,
                    "message": {"mid": "echo-2", "text": "x", "is_echo": True},
                }
            ],
        }
        process_entry("instagram", echo_entry)
        assert len(sent) == 1
