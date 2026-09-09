"""
Phase 1 — Meta Inbox connection plumbing.

Covers the OAuth handler wiring, the encrypted page-token round trip, tenant
uniqueness of page_id/ig_user_id (the webhook's only tenant hint), and the
/inbox/connections/ endpoints.
"""

import pytest

from conftest import api_body

pytestmark = pytest.mark.django_db

CONNECTIONS_URL = "/api/v1/integrations/inbox/connections/"


def _detail_url(pk):
    return f"/api/v1/integrations/inbox/connections/{pk}/"


class TestMetaInboxOAuthHandler:
    def test_handler_resolves_for_meta_inbox_platform(self, settings):
        from integrations.oauth_utils import MetaInboxOAuth, get_oauth_handler

        settings.META_INBOX_CLIENT_ID = "inbox-app-id"
        settings.META_INBOX_CLIENT_SECRET = "inbox-app-secret"

        handler = get_oauth_handler("meta_inbox")
        assert isinstance(handler, MetaInboxOAuth)

    def test_credentials_come_from_meta_inbox_settings_not_meta(self, settings):
        """A shared secret would let the Lead Ads app forge inbox webhooks."""
        from integrations.oauth_utils import MetaInboxOAuth

        settings.META_CLIENT_ID = "leadads-app-id"
        settings.META_CLIENT_SECRET = "leadads-app-secret"
        settings.META_INBOX_CLIENT_ID = "inbox-app-id"
        settings.META_INBOX_CLIENT_SECRET = "inbox-app-secret"

        handler = MetaInboxOAuth()
        assert handler.client_id == "inbox-app-id"
        assert handler.client_secret == "inbox-app-secret"

    def test_authorization_url_uses_config_id_and_no_scope(self, settings):
        from integrations.oauth_utils import MetaInboxOAuth

        settings.META_INBOX_CLIENT_ID = "inbox-app-id"
        settings.META_INBOX_FACEBOOK_LOGIN_FOR_BUSINESS_CONFIG_ID = "cfg-123"

        url = MetaInboxOAuth().get_authorization_url("state-abc")
        assert "config_id=cfg-123" in url
        assert "override_default_response_type=true" in url
        assert "scope=" not in url

    def test_authorization_url_requires_config_id(self, settings):
        """Without the config the dialog grants nothing useful — fail loudly."""
        from integrations.oauth_utils import MetaInboxOAuth

        settings.META_INBOX_FACEBOOK_LOGIN_FOR_BUSINESS_CONFIG_ID = ""
        with pytest.raises(ValueError):
            MetaInboxOAuth().get_authorization_url("state-abc")

    def test_blank_graph_version_falls_back(self, settings):
        from integrations.oauth_utils import MetaInboxOAuth

        settings.META_INBOX_GRAPH_API_VERSION = ""
        handler = MetaInboxOAuth()
        assert handler.graph_api_url.startswith("https://graph.facebook.com/v")


class TestMetaInboxConnectionModel:
    def test_page_token_round_trips_encrypted(self, meta_inbox_connection):
        from integrations.models import MetaInboxConnection

        row = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert row.get_page_access_token() == "page-token-xyz"
        # The stored column must not be the plaintext token.
        assert row.page_access_token != "page-token-xyz"

    def test_page_id_is_globally_unique(self, company, meta_inbox_connection, other_company):
        """The webhook resolves the tenant from entry.id alone, so it must be unambiguous."""
        from django.db.utils import IntegrityError
        from integrations.models import MetaInboxConnection

        with pytest.raises(IntegrityError):
            MetaInboxConnection.objects.create(
                company=other_company,
                page_id=meta_inbox_connection.page_id,
            )

    def test_ig_user_id_nullable_for_messenger_only_tenant(self, company):
        from integrations.models import MetaInboxConnection

        first = MetaInboxConnection.objects.create(
            company=company, page_id="300000000000001", ig_user_id=None
        )
        second = MetaInboxConnection.objects.create(
            company=company, page_id="300000000000002", ig_user_id=None
        )
        # Two NULL ig_user_id rows must coexist despite the unique constraint.
        assert first.pk != second.pk


class TestConnectionsEndpointAccess:
    def test_owner_can_list(self, authenticated_admin, meta_inbox_account):
        response = authenticated_admin.get(CONNECTIONS_URL)
        assert response.status_code == 200

    def test_call_center_cannot_manage_connections(self, authenticated_call_center):
        """Inbox usage is for call_center; account configuration is not."""
        response = authenticated_call_center.get(CONNECTIONS_URL)
        assert response.status_code == 403
        assert response.data["error"]["code"] == "meta_inbox_admin_only"

    def test_employee_cannot_manage_connections(self, authenticated_employee):
        response = authenticated_employee.get(CONNECTIONS_URL)
        assert response.status_code == 403

    def test_unauthenticated_denied(self, api_client):
        response = api_client.get(CONNECTIONS_URL)
        assert response.status_code in (401, 403)

    def test_plan_gate_blocks_when_feature_disabled(
        self, authenticated_admin, meta_inbox_account, monkeypatch
    ):
        monkeypatch.setattr(
            "integrations.views.social_inbox.get_plan_integration_access",
            lambda company, platform: {
                "enabled": False,
                "message": "Not in your plan.",
                "scope": "plan",
                "feature_key": "integration_meta_inbox",
            },
        )
        response = authenticated_admin.get(CONNECTIONS_URL)
        assert response.status_code == 403
        assert response.data["error"]["code"] == "plan_integration_disabled"


class TestConnectionsList:
    def test_lists_connections_without_leaking_page_token(
        self, authenticated_admin, meta_inbox_connection, monkeypatch
    ):
        monkeypatch.setattr(
            "integrations.views.social_inbox.list_grantable_pages", lambda account: []
        )
        response = authenticated_admin.get(CONNECTIONS_URL)
        assert response.status_code == 200
        body = api_body(response)

        assert len(body["connections"]) == 1
        connection = body["connections"][0]
        assert connection["page_id"] == "100000000000001"
        assert connection["ig_username"] == "testbiz"
        assert "page_access_token" not in connection

    def test_available_pages_exclude_already_connected(
        self, authenticated_admin, meta_inbox_connection, monkeypatch
    ):
        monkeypatch.setattr(
            "integrations.views.social_inbox.list_grantable_pages",
            lambda account: [
                {"id": "100000000000001", "name": "Test Page"},
                {"id": "100000000000002", "name": "Second Page"},
            ],
        )
        body = api_body(authenticated_admin.get(CONNECTIONS_URL))
        available_ids = [p["id"] for p in body["available_pages"]]
        assert available_ids == ["100000000000002"]

    def test_cross_tenant_connections_are_invisible(
        self, authenticated_admin, meta_inbox_account, other_company_inbox_connection, monkeypatch
    ):
        monkeypatch.setattr(
            "integrations.views.social_inbox.list_grantable_pages", lambda account: []
        )
        body = api_body(authenticated_admin.get(CONNECTIONS_URL))
        assert body["connections"] == []


class TestConnectPage:
    def test_connect_page_stores_token_and_subscribes(
        self, authenticated_admin, meta_inbox_account, monkeypatch
    ):
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth, "get_page_access_token", lambda self, p, t: "fresh-page-token"
        )
        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_page_instagram_account",
            lambda self, p, t: {"id": "170000000000009", "username": "shopig"},
        )
        monkeypatch.setattr(
            MetaInboxOAuth, "subscribe_page", lambda self, p, t, fields=None: {"success": True}
        )

        response = authenticated_admin.post(
            CONNECTIONS_URL, {"page_id": "100000000000001"}, format="json"
        )
        assert response.status_code == 201

        connection = MetaInboxConnection.objects.get(page_id="100000000000001")
        assert connection.company_id == meta_inbox_account.company_id
        assert connection.get_page_access_token() == "fresh-page-token"
        assert connection.ig_user_id == "170000000000009"
        assert connection.instagram_subscribed is True
        assert connection.messenger_subscribed is True
        assert connection.status == "connected"

    def test_messenger_only_page_connects_without_instagram(
        self, authenticated_admin, meta_inbox_account, monkeypatch
    ):
        """A Page with no linked IG account is a supported setup, not an error."""
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth, "get_page_access_token", lambda self, p, t: "fresh-page-token"
        )
        monkeypatch.setattr(
            MetaInboxOAuth, "get_page_instagram_account", lambda self, p, t: None
        )
        monkeypatch.setattr(
            MetaInboxOAuth, "subscribe_page", lambda self, p, t, fields=None: {"success": True}
        )

        response = authenticated_admin.post(
            CONNECTIONS_URL, {"page_id": "100000000000001"}, format="json"
        )
        assert response.status_code == 201

        connection = MetaInboxConnection.objects.get(page_id="100000000000001")
        assert connection.ig_user_id is None
        assert connection.instagram_subscribed is False
        assert connection.messenger_subscribed is True

    def test_page_owned_by_another_tenant_is_rejected(
        self, authenticated_admin, meta_inbox_account, other_company_inbox_connection, monkeypatch
    ):
        """Silently retargeting another tenant's Page would hand them our webhooks."""
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth, "get_page_access_token", lambda self, p, t: "fresh-page-token"
        )
        response = authenticated_admin.post(
            CONNECTIONS_URL, {"page_id": "200000000000001"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "meta_inbox_page_taken"

    def test_missing_page_token_surfaces_actionable_error(
        self, authenticated_admin, meta_inbox_account, monkeypatch
    ):
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(MetaInboxOAuth, "get_page_access_token", lambda self, p, t: None)
        response = authenticated_admin.post(
            CONNECTIONS_URL, {"page_id": "100000000000001"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "meta_inbox_page_token_unavailable"

    def test_failed_subscription_marks_connection_error(
        self, authenticated_admin, meta_inbox_account, monkeypatch
    ):
        """Meta accepts sends on an unsubscribed Page but delivers no webhooks."""
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth, "get_page_access_token", lambda self, p, t: "fresh-page-token"
        )
        monkeypatch.setattr(
            MetaInboxOAuth, "get_page_instagram_account", lambda self, p, t: None
        )
        monkeypatch.setattr(
            MetaInboxOAuth,
            "subscribe_page",
            lambda self, p, t, fields=None: {"error": {"message": "boom"}},
        )

        authenticated_admin.post(
            CONNECTIONS_URL, {"page_id": "100000000000001"}, format="json"
        )
        connection = MetaInboxConnection.objects.get(page_id="100000000000001")
        assert connection.status == "error"
        assert connection.messenger_subscribed is False

    def test_page_id_required(self, authenticated_admin, meta_inbox_account):
        response = authenticated_admin.post(CONNECTIONS_URL, {}, format="json")
        assert response.status_code == 400
        assert response.data["error"]["code"] == "meta_inbox_page_required"

    def test_connect_requires_connected_account(self, authenticated_admin, company):
        response = authenticated_admin.post(
            CONNECTIONS_URL, {"page_id": "100000000000001"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "meta_inbox_not_connected"


class TestDisconnectAndHealth:
    def test_disconnect_clears_token_and_keeps_row(
        self, authenticated_admin, meta_inbox_connection, monkeypatch
    ):
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth, "unsubscribe_page", lambda self, p, t: {"success": True}
        )
        response = authenticated_admin.delete(_detail_url(meta_inbox_connection.pk))
        assert response.status_code == 200

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "disconnected"
        assert connection.get_page_access_token() is None
        assert connection.messenger_subscribed is False

    def test_list_omits_disconnected_pages(
        self, authenticated_admin, meta_inbox_connection, monkeypatch
    ):
        from integrations.models import MetaInboxConnection

        monkeypatch.setattr(
            "integrations.views.social_inbox.list_grantable_pages", lambda account: []
        )
        MetaInboxConnection.objects.filter(pk=meta_inbox_connection.pk).update(
            status="disconnected"
        )
        body = api_body(authenticated_admin.get(CONNECTIONS_URL))
        assert body["connections"] == []

    def test_disconnect_account_disconnects_pages(
        self, authenticated_admin, meta_inbox_account, meta_inbox_connection, monkeypatch
    ):
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth, "unsubscribe_page", lambda self, p, t: {"success": True}
        )
        response = authenticated_admin.post(
            f"/api/v1/integrations/accounts/{meta_inbox_account.id}/disconnect/"
        )
        assert response.status_code == 200
        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "disconnected"
        assert connection.get_page_access_token() is None

    def test_cannot_disconnect_another_tenants_connection(
        self, authenticated_admin, other_company_inbox_connection
    ):
        response = authenticated_admin.delete(
            _detail_url(other_company_inbox_connection.pk)
        )
        assert response.status_code == 404

    def test_health_check_flags_silent_unsubscribe(
        self, authenticated_admin, meta_inbox_connection, monkeypatch
    ):
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth

        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_subscribed_fields",
            lambda self, p, t: (False, [], None),
        )
        response = authenticated_admin.post(_detail_url(meta_inbox_connection.pk))
        assert response.status_code == 200
        assert api_body(response)["health"]["ok"] is False

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "error"

    def test_health_check_recovers_connection(
        self, authenticated_admin, meta_inbox_connection, monkeypatch
    ):
        from integrations.models import MetaInboxConnection
        from integrations.oauth_utils import MetaInboxOAuth

        meta_inbox_connection.status = "error"
        meta_inbox_connection.error_message = "stale"
        meta_inbox_connection.save(update_fields=["status", "error_message"])

        monkeypatch.setattr(
            MetaInboxOAuth,
            "get_subscribed_fields",
            lambda self, p, t: (True, ["messages", "messaging_postbacks"], None),
        )
        authenticated_admin.post(_detail_url(meta_inbox_connection.pk))

        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "connected"
        assert connection.error_message is None


class TestPolicySideEffects:
    def test_disabling_platform_disconnects_connections(self, meta_inbox_connection, company):
        from integrations.models import MetaInboxConnection
        from integrations.policy import apply_integration_policy_side_effects

        apply_integration_policy_side_effects(
            previous_policies={"meta_inbox": {"global_enabled": True}},
            new_policies={"meta_inbox": {"global_enabled": False}},
        )
        connection = MetaInboxConnection.objects.get(pk=meta_inbox_connection.pk)
        assert connection.status == "disconnected"

    def test_meta_inbox_is_a_policy_platform(self):
        from integrations.policy import (
            INTEGRATION_POLICY_PLATFORMS,
            PLAN_INTEGRATION_FEATURE_MAP,
        )

        assert "meta_inbox" in INTEGRATION_POLICY_PLATFORMS
        assert PLAN_INTEGRATION_FEATURE_MAP["meta_inbox"] == "integration_meta_inbox"
