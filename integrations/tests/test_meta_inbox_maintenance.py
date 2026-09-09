"""
Scheduled maintenance for the Omni-Channel Inbox.

Covers the three P1 gaps from docs/OMNI_CHANNEL_INBOX_HANDOFF.md: the media
retention purge, the page-subscription health sweep, and page-token re-derivation
after a user-token refresh — plus the token-refresh cron itself, which imported
two functions that were never written and so had never once run.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone

pytestmark = pytest.mark.django_db


def run(command, *args):
    out = StringIO()
    call_command(command, *args, stdout=out, stderr=out)
    return out.getvalue()


@pytest.fixture
def inbox_credentials(settings):
    """
    debug_token needs both app credentials; without them check_account_token
    deliberately answers "unknown" instead of "invalid".
    """
    settings.META_INBOX_CLIENT_ID = "inbox-app-id"
    settings.META_INBOX_CLIENT_SECRET = "inbox-app-secret"
    settings.META_CLIENT_ID = "leadads-app-id"
    settings.META_CLIENT_SECRET = "leadads-app-secret"
    return settings


@pytest.fixture
def no_page_token_calls(monkeypatch):
    """
    Stop refresh_meta_inbox_page_tokens from reaching Graph.

    apply_refreshed_token now re-derives page tokens, so every test that refreshes
    a meta_inbox user token would otherwise make a real HTTP call.
    """
    from integrations.oauth_utils import MetaInboxOAuth

    calls = []

    def _fake(self, page_id, user_token):
        calls.append((page_id, user_token))
        return f"page-token-for-{page_id}"

    monkeypatch.setattr(MetaInboxOAuth, "get_page_access_token", _fake)
    return calls


@pytest.fixture
def captured_notifications(monkeypatch):
    """Record owner notifications instead of sending email + FCM."""
    from integrations.services import token_lifecycle

    sent = []
    monkeypatch.setattr(
        token_lifecycle,
        "notify_owner_token_invalid",
        lambda account, reason="": sent.append((account.id, reason)) or True,
    )
    return sent


def due_soon(account, days=2):
    account.token_expires_at = timezone.now() + timedelta(days=days)
    account.save(update_fields=["token_expires_at"])
    return account


# --- the cron that never ran ---------------------------------------------------


class TestRefreshIntegrationTokensCommand:
    def test_command_is_importable(self):
        """
        Regression: the command imported refresh_expired_tokens and
        validate_meta_tokens from integrations.tasks, where neither was ever
        defined. Every scheduled run died with ImportError before doing any work,
        so no Meta, WhatsApp or inbox token has ever been refreshed on a schedule.
        """
        output = run("refresh_integration_tokens", "--dry-run")
        assert "DRY RUN" in output

    def test_dry_run_counts_only_refreshable_platforms(
        self, meta_inbox_account, company, db
    ):
        from integrations.models import IntegrationAccount, IntegrationPlatform

        due_soon(meta_inbox_account)
        # tiktok has no OAuth handler at all; counting it would promise a refresh
        # the real run cannot perform.
        tiktok = IntegrationAccount.objects.create(
            company=company,
            platform=IntegrationPlatform.TIKTOK,
            name="TikTok",
            status="connected",
            external_account_id="tt-1",
        )
        due_soon(tiktok)

        assert "Would refresh ~1 account(s)" in run(
            "refresh_integration_tokens", "--dry-run"
        )

    def test_dry_run_writes_nothing(self, meta_inbox_account, inbox_credentials):
        due_soon(meta_inbox_account)
        before = meta_inbox_account.get_access_token()
        run("refresh_integration_tokens", "--dry-run")
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.get_access_token() == before


class TestRefreshExpiredTokens:
    def test_reexchanges_token_nearing_expiry(
        self, meta_inbox_account, monkeypatch, no_page_token_calls
    ):
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import refresh_expired_tokens

        due_soon(meta_inbox_account)
        monkeypatch.setattr(
            MetaInboxOAuth,
            "refresh_token",
            lambda self, token: {"access_token": "renewed-token", "expires_in": 5184000},
        )

        assert refresh_expired_tokens()["refreshed"] == 1
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.get_access_token() == "renewed-token"
        assert meta_inbox_account.token_expires_at > timezone.now() + timedelta(days=50)

    def test_account_far_from_expiry_is_left_alone(
        self, meta_inbox_account, monkeypatch, no_page_token_calls
    ):
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import refresh_expired_tokens

        due_soon(meta_inbox_account, days=45)
        monkeypatch.setattr(
            MetaInboxOAuth,
            "refresh_token",
            lambda self, token: pytest.fail("must not refresh a token good for 45 days"),
        )
        assert refresh_expired_tokens() == {"refreshed": 0, "expired": 0, "failed": 0}

    def test_platform_without_oauth_handler_is_never_touched(
        self, company, captured_notifications
    ):
        """
        A tiktok/api/mujeb account raises from get_oauth_handler. Including those
        platforms would turn every run into a false "your integration expired"
        email to a tenant whose integration is fine.
        """
        from integrations.models import IntegrationAccount, IntegrationPlatform
        from integrations.services.token_lifecycle import refresh_expired_tokens

        tiktok = due_soon(
            IntegrationAccount.objects.create(
                company=company,
                platform=IntegrationPlatform.TIKTOK,
                name="TikTok",
                status="connected",
                external_account_id="tt-1",
            )
        )

        assert refresh_expired_tokens() == {"refreshed": 0, "expired": 0, "failed": 0}
        tiktok.refresh_from_db()
        assert tiktok.status == "connected"
        assert captured_notifications == []

    def test_transient_graph_failure_does_not_expire_a_valid_token(
        self, meta_inbox_account, inbox_credentials, monkeypatch, captured_notifications
    ):
        """
        A Graph hiccup must not flip a healthy account to expired and email the
        owner — the token is still good for days and the next run retries.
        """
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import refresh_expired_tokens

        due_soon(meta_inbox_account)
        monkeypatch.setattr(
            MetaInboxOAuth,
            "refresh_token",
            lambda self, token: (_ for _ in ()).throw(RuntimeError("HTTP 503")),
        )
        monkeypatch.setattr(
            MetaInboxOAuth, "debug_token", lambda self, token: {"is_valid": True}
        )

        assert refresh_expired_tokens() == {"refreshed": 0, "expired": 0, "failed": 1}
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.status == "connected"
        assert captured_notifications == []

    def test_confirmed_dead_token_expires_and_notifies(
        self, meta_inbox_account, inbox_credentials, monkeypatch, captured_notifications
    ):
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import refresh_expired_tokens

        due_soon(meta_inbox_account)
        monkeypatch.setattr(
            MetaInboxOAuth,
            "refresh_token",
            lambda self, token: (_ for _ in ()).throw(RuntimeError("code 190")),
        )
        monkeypatch.setattr(
            MetaInboxOAuth,
            "debug_token",
            lambda self, token: {
                "is_valid": False,
                "error": {"code": 190, "message": "Session has been invalidated"},
            },
        )

        assert refresh_expired_tokens()["expired"] == 1
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.status == "expired"
        assert "Session has been invalidated" in meta_inbox_account.error_message
        assert len(captured_notifications) == 1


class TestValidateMetaTokens:
    def test_revoked_token_is_marked_expired(
        self, meta_inbox_account, inbox_credentials, monkeypatch, captured_notifications
    ):
        """A token revoked from Facebook settings dies long before token_expires_at."""
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import validate_meta_tokens

        monkeypatch.setattr(
            MetaInboxOAuth,
            "debug_token",
            lambda self, token: {
                "is_valid": False,
                "error": {"code": 190, "message": "Token revoked"},
            },
        )

        assert validate_meta_tokens()["expired"] == 1
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.status == "expired"
        assert len(captured_notifications) == 1

    def test_valid_token_is_left_connected(
        self, meta_inbox_account, inbox_credentials, monkeypatch, captured_notifications
    ):
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import validate_meta_tokens

        monkeypatch.setattr(
            MetaInboxOAuth, "debug_token", lambda self, token: {"is_valid": True}
        )
        result = validate_meta_tokens()
        assert (result["valid"], result["expired"]) == (1, 0)
        assert captured_notifications == []

    def test_unreachable_graph_leaves_account_connected(
        self, meta_inbox_account, inbox_credentials, monkeypatch, captured_notifications
    ):
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import validate_meta_tokens

        monkeypatch.setattr(
            MetaInboxOAuth,
            "debug_token",
            lambda self, token: (_ for _ in ()).throw(RuntimeError("connection reset")),
        )

        assert validate_meta_tokens()["unknown"] == 1
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.status == "connected"
        assert captured_notifications == []

    def test_unconfigured_app_credentials_expire_nothing(
        self, meta_inbox_account, settings, monkeypatch, captured_notifications
    ):
        """
        debug_token reports is_valid=False when it cannot obtain an APP token —
        its own failure, not the tenant's. Reading that as a verdict would expire
        every account on the platform and email every owner.
        """
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import validate_meta_tokens

        settings.META_INBOX_CLIENT_ID = ""
        settings.META_INBOX_CLIENT_SECRET = ""
        monkeypatch.setattr(
            MetaInboxOAuth,
            "debug_token",
            lambda self, token: pytest.fail("must not call Graph without app credentials"),
        )

        assert validate_meta_tokens()["unknown"] == 1
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.status == "connected"
        assert captured_notifications == []

    def test_app_token_failure_sentinel_is_not_a_verdict(
        self, meta_inbox_account, inbox_credentials, monkeypatch, captured_notifications
    ):
        """
        The handler's own sentinel puts a *string* in `error`; Graph's
        invalid-token answer nests a dict. Only the latter expires an account.
        """
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import validate_meta_tokens

        monkeypatch.setattr(
            MetaInboxOAuth,
            "debug_token",
            lambda self, token: {
                "is_valid": False,
                "error": "Could not get app access token",
            },
        )

        assert validate_meta_tokens()["unknown"] == 1
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.status == "connected"
        assert captured_notifications == []


# --- P1: page tokens are derived from the user token ---------------------------


class TestPageTokenRederivation:
    def test_user_token_refresh_rederives_page_tokens(
        self, meta_inbox_account, meta_inbox_connection, monkeypatch
    ):
        """
        Page tokens are minted from the user token. Re-exchanging the user token
        and stopping there leaves them tied to a token that no longer exists, and
        the inbox goes quiet with no error anywhere.
        """
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import refresh_expired_tokens

        due_soon(meta_inbox_account)
        monkeypatch.setattr(
            MetaInboxOAuth,
            "refresh_token",
            lambda self, token: {"access_token": "renewed-user-token", "expires_in": 5184000},
        )
        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_page_access_token",
            lambda self, page_id, user_token: "rederived-page-token",
        )

        refresh_expired_tokens()

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.get_page_access_token() == "rederived-page-token"
        assert connection.token_expires_at is not None

    def test_identical_token_is_not_rewritten(
        self, meta_inbox_account, meta_inbox_connection, monkeypatch
    ):
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.meta_inbox_connections import (
            refresh_meta_inbox_page_tokens,
        )

        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_page_access_token",
            lambda self, page_id, user_token: "page-token-xyz",
        )
        result = refresh_meta_inbox_page_tokens(meta_inbox_account)
        assert result == {"updated": 0, "unchanged": 1, "failed": 0}

    def test_failure_keeps_the_existing_page_token(
        self, meta_inbox_account, meta_inbox_connection, monkeypatch
    ):
        """The old token is still the best credential we have; the next run retries."""
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.meta_inbox_connections import (
            refresh_meta_inbox_page_tokens,
        )

        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_page_access_token",
            lambda self, page_id, user_token: (_ for _ in ()).throw(RuntimeError("HTTP 500")),
        )
        assert refresh_meta_inbox_page_tokens(meta_inbox_account)["failed"] == 1

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.get_page_access_token() == "page-token-xyz"
        assert connection.status == "connected"

    def test_disconnected_connection_is_skipped(
        self, meta_inbox_account, meta_inbox_connection, monkeypatch
    ):
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.meta_inbox_connections import (
            refresh_meta_inbox_page_tokens,
        )

        meta_inbox_connection.status = "disconnected"
        meta_inbox_connection.save(update_fields=["status"])
        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_page_access_token",
            lambda self, page_id, user_token: pytest.fail(
                "a disconnected Page must not be re-credentialed"
            ),
        )
        assert refresh_meta_inbox_page_tokens(meta_inbox_account) == {
            "updated": 0,
            "unchanged": 0,
            "failed": 0,
        }

    def test_page_token_failure_does_not_undo_the_user_token_refresh(
        self, meta_inbox_account, meta_inbox_connection, monkeypatch
    ):
        """The user-token refresh already succeeded and must be kept."""
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.token_lifecycle import refresh_expired_tokens

        due_soon(meta_inbox_account)
        monkeypatch.setattr(
            MetaInboxOAuth,
            "refresh_token",
            lambda self, token: {"access_token": "renewed-user-token", "expires_in": 5184000},
        )
        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_page_access_token",
            lambda self, page_id, user_token: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert refresh_expired_tokens()["refreshed"] == 1
        meta_inbox_account.refresh_from_db()
        assert meta_inbox_account.get_access_token() == "renewed-user-token"

    def test_cross_tenant_connections_are_untouched(
        self, meta_inbox_account, other_company_inbox_connection, monkeypatch
    ):
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth
        from integrations.services.meta_inbox_connections import (
            refresh_meta_inbox_page_tokens,
        )

        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_page_access_token",
            lambda self, page_id, user_token: "leaked-token",
        )
        refresh_meta_inbox_page_tokens(meta_inbox_account)

        other = MetaInboxConnection.objects.get(pk=other_company_inbox_connection.pk)
        assert other.get_page_access_token() == "other-page-token"


# --- P1: page-subscription health sweep ----------------------------------------


@pytest.fixture
def captured_health_notifications(monkeypatch):
    from integrations.management.commands import check_meta_inbox_connections as cmd

    sent = []
    monkeypatch.setattr(
        cmd, "notify_owner_inbox_connection_broken", lambda c: sent.append(c.id) or True
    )
    return sent


def stub_subscribed_fields(monkeypatch, result):
    from integrations.oauth_utils import MetaInboxOAuth

    monkeypatch.setattr(
        MetaInboxOAuth, "get_subscribed_fields", lambda self, page_id, token: result
    )


class TestConnectionHealthCommand:
    def test_broken_page_is_flagged_and_the_owner_notified(
        self, meta_inbox_connection, monkeypatch, captured_health_notifications
    ):
        """
        Meta keeps accepting sends on a Page whose subscription was removed in
        Business Settings while delivering no webhooks, so nothing else surfaces
        this until someone notices the inbox went quiet.
        """
        from integrations.models import MetaInboxConnection

        stub_subscribed_fields(monkeypatch, (False, [], None))
        output = run("check_meta_inbox_connections")

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "error"
        assert captured_health_notifications == [meta_inbox_connection.id]
        assert "1 broken" in output

    def test_owner_is_notified_on_the_transition_only(
        self, meta_inbox_connection, monkeypatch, captured_health_notifications
    ):
        """
        The error status persists between runs, so alerting on state rather than
        on change would re-alert the owner every day until they reconnected.
        """
        stub_subscribed_fields(monkeypatch, (False, [], None))
        run("check_meta_inbox_connections")
        run("check_meta_inbox_connections")
        assert captured_health_notifications == [meta_inbox_connection.id]

    def test_recovered_page_returns_to_connected(
        self, meta_inbox_connection, monkeypatch, captured_health_notifications
    ):
        from integrations.models import MetaInboxConnection

        meta_inbox_connection.status = "error"
        meta_inbox_connection.error_message = "was broken"
        meta_inbox_connection.save(update_fields=["status", "error_message"])

        stub_subscribed_fields(monkeypatch, (True, ["messages"], None))
        output = run("check_meta_inbox_connections")

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "connected"
        assert connection.error_message is None
        assert "1 recovered" in output
        assert captured_health_notifications == []

    def test_graph_error_changes_nothing_and_notifies_nobody(
        self, meta_inbox_connection, monkeypatch, captured_health_notifications
    ):
        """One bad afternoon at Meta must not email every owner on the platform."""
        from integrations.models import MetaInboxConnection

        stub_subscribed_fields(monkeypatch, (False, [], "temporarily unavailable"))
        output = run("check_meta_inbox_connections")

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "connected"
        assert captured_health_notifications == []
        assert "1 inconclusive" in output

    def test_missing_page_token_is_reported_as_broken(
        self, meta_inbox_connection, monkeypatch, captured_health_notifications
    ):
        meta_inbox_connection.set_page_access_token(None)
        meta_inbox_connection.save(update_fields=["page_access_token"])

        stub_subscribed_fields(
            monkeypatch, (True, ["messages"], None)
        )  # never reached
        run("check_meta_inbox_connections")
        assert captured_health_notifications == [meta_inbox_connection.id]

    def test_disconnected_pages_are_skipped(
        self, meta_inbox_connection, monkeypatch, captured_health_notifications
    ):
        """The tenant, or a plan change, switched those off deliberately."""
        from integrations.oauth_utils import MetaInboxOAuth

        meta_inbox_connection.status = "disconnected"
        meta_inbox_connection.save(update_fields=["status"])
        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_subscribed_fields",
            lambda self, page_id, token: pytest.fail(
                "a disconnected Page must not be health-checked"
            ),
        )
        run("check_meta_inbox_connections")
        assert captured_health_notifications == []

    def test_company_id_scopes_the_sweep(
        self,
        meta_inbox_connection,
        other_company_inbox_connection,
        monkeypatch,
        captured_health_notifications,
    ):
        from integrations.models import MetaInboxConnection

        stub_subscribed_fields(monkeypatch, (False, [], None))
        run(
            "check_meta_inbox_connections",
            "--company-id",
            str(other_company_inbox_connection.company_id),
        )

        assert (
            MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk).status
            == "connected"
        )
        assert captured_health_notifications == [other_company_inbox_connection.id]

    def test_dry_run_calls_no_graph(self, meta_inbox_connection, monkeypatch):
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_subscribed_fields",
            lambda self, page_id, token: pytest.fail("dry run must not call Graph"),
        )
        assert "Would check 1 connection(s)" in run(
            "check_meta_inbox_connections", "--dry-run"
        )


# --- P1: media retention purge -------------------------------------------------


@pytest.fixture
def conversation(company, meta_inbox_connection, db):
    from integrations.models import SocialContact, SocialConversation

    contact = SocialContact.objects.create(
        company=company,
        connection=meta_inbox_connection,
        channel="instagram",
        external_id="IGSID-PURGE",
        username="media_sender",
    )
    return SocialConversation.objects.create(
        company=company,
        connection=meta_inbox_connection,
        contact=contact,
        channel="instagram",
    )


def message_with_media(conversation, *, age_days, mid="att-1", data=b"jpeg-bytes"):
    """A stored attachment whose row is backdated past created_at's auto_now_add."""
    from integrations.models import SocialMessage

    message = SocialMessage.objects.create(
        conversation=conversation,
        direction=SocialMessage.DIRECTION_INBOUND,
        external_message_id=mid,
        body="look at this",
        attachment_kind="image",
        attachment_mime="image/jpeg",
    )
    message.attachment.save("shot.jpg", ContentFile(data), save=False)
    message.attachment_size = len(data)
    message.save(update_fields=["attachment", "attachment_size"])
    SocialMessage.objects.filter(pk=message.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    message.refresh_from_db()
    return message


class TestPurgeSocialMedia:
    def test_purges_stored_bytes_and_keeps_the_message(self, conversation):
        from django.core.files.storage import default_storage
        from integrations.models import SocialMessage

        message = message_with_media(conversation, age_days=120)
        path = message.attachment.name
        assert default_storage.exists(path)

        run("purge_social_media", "--days", "90")

        message.refresh_from_db()
        assert not message.attachment
        assert message.attachment_size is None
        assert not default_storage.exists(path)
        # The thread must still render the bubble and its caption.
        assert SocialMessage.objects.filter(pk=message.pk).exists()
        assert message.body == "look at this"
        assert message.attachment_kind == "image"

    def test_attachment_inside_the_window_is_kept(self, conversation):
        message = message_with_media(conversation, age_days=10)
        run("purge_social_media", "--days", "90")
        message.refresh_from_db()
        assert message.attachment
        assert message.attachment_size == len(b"jpeg-bytes")

    def test_retention_default_comes_from_settings(self, conversation, settings):
        settings.META_INBOX_MEDIA_RETENTION_DAYS = 7
        message = message_with_media(conversation, age_days=30)
        run("purge_social_media")
        message.refresh_from_db()
        assert not message.attachment

    def test_dry_run_deletes_nothing(self, conversation):
        from django.core.files.storage import default_storage

        message = message_with_media(conversation, age_days=120)
        path = message.attachment.name

        output = run("purge_social_media", "--days", "90", "--dry-run")

        assert "Would purge 1 attachment(s)" in output
        message.refresh_from_db()
        assert message.attachment
        assert default_storage.exists(path)

    def test_company_id_scopes_the_purge(
        self, conversation, other_company, other_company_inbox_connection, db
    ):
        from integrations.models import SocialContact, SocialConversation

        contact = SocialContact.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            channel="instagram",
            external_id="OTHER-PURGE",
        )
        other_conversation = SocialConversation.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            contact=contact,
            channel="instagram",
        )
        ours = message_with_media(conversation, age_days=120, mid="ours")
        theirs = message_with_media(other_conversation, age_days=120, mid="theirs")

        run("purge_social_media", "--days", "90", "--company-id", str(other_company.id))

        ours.refresh_from_db()
        theirs.refresh_from_db()
        assert ours.attachment
        assert not theirs.attachment

    def test_text_only_message_is_untouched(self, conversation):
        from integrations.models import SocialMessage

        message = SocialMessage.objects.create(
            conversation=conversation,
            direction=SocialMessage.DIRECTION_INBOUND,
            external_message_id="text-only",
            body="no media here",
        )
        SocialMessage.objects.filter(pk=message.pk).update(
            created_at=timezone.now() - timedelta(days=120)
        )
        assert "Purged 0 attachment(s)" in run("purge_social_media", "--days", "90")

    def test_already_missing_file_still_clears_the_row(self, conversation):
        """
        A row pointing at a file that is already gone is the reason the query keeps
        finding it, so the pointer must be cleared either way.
        """
        from django.core.files.storage import default_storage

        message = message_with_media(conversation, age_days=120)
        default_storage.delete(message.attachment.name)

        run("purge_social_media", "--days", "90")

        message.refresh_from_db()
        assert not message.attachment
        assert message.attachment_size is None

    def test_purge_bumps_the_inbox_slice(self, conversation, company):
        """
        Invariant: a write that changes what the inbox list renders must move the
        `inbox` slice, or clients 304 and keep showing a dead attachment URL.
        """
        from sync.version import slice_versions

        message_with_media(conversation, age_days=120)
        owner = company.owner
        before = slice_versions(owner)["inbox"]

        run("purge_social_media", "--days", "90")

        assert slice_versions(owner)["inbox"] > before

    def test_days_must_be_positive(self, conversation):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            run("purge_social_media", "--days", "0")

    def test_purges_across_multiple_companies(
        self, conversation, other_company, other_company_inbox_connection, db
    ):
        from integrations.models import SocialContact, SocialConversation

        contact = SocialContact.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            channel="instagram",
            external_id="OTHER-BATCH",
        )
        other_conversation = SocialConversation.objects.create(
            company=other_company,
            connection=other_company_inbox_connection,
            contact=contact,
            channel="instagram",
        )
        ours = message_with_media(conversation, age_days=120, mid="ours")
        theirs = message_with_media(other_conversation, age_days=120, mid="theirs")

        assert "Purged 2 attachment(s)" in run("purge_social_media", "--days", "90")
        ours.refresh_from_db()
        theirs.refresh_from_db()
        assert not ours.attachment
        assert not theirs.attachment
