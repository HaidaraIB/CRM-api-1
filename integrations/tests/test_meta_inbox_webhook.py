"""
Phase 2 — Meta Inbox webhook transport.

Signature, verification, and the always-200 contract. The most important case
here is that the OLD Meta app's secret cannot sign an inbox webhook.
"""

import hashlib
import hmac
import json

import pytest

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/integrations/webhooks/meta-inbox/"

INBOX_SECRET = "inbox-app-secret"
LEADADS_SECRET = "leadads-app-secret"


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_payload(client, payload, *, secret=INBOX_SECRET, signature=None):
    body = json.dumps(payload).encode()
    headers = {}
    if signature is not False:
        headers["HTTP_X_HUB_SIGNATURE_256"] = signature or sign(body, secret)
    return client.post(
        WEBHOOK_URL, data=body, content_type="application/json", **headers
    )


@pytest.fixture(autouse=True)
def _inbox_app_secrets(settings):
    settings.META_INBOX_CLIENT_SECRET = INBOX_SECRET
    settings.META_CLIENT_SECRET = LEADADS_SECRET
    settings.META_INBOX_WEBHOOK_VERIFY_TOKEN = "verify-me"
    settings.META_INBOX_WEBHOOK_ALLOWED_IPS = None


class TestWebhookVerification:
    def test_correct_token_returns_challenge(self, client):
        response = client.get(
            WEBHOOK_URL,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-123",
            },
        )
        assert response.status_code == 200
        assert response.content.decode() == "challenge-123"

    def test_wrong_token_forbidden(self, client):
        response = client.get(
            WEBHOOK_URL,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "nope",
                "hub.challenge": "challenge-123",
            },
        )
        assert response.status_code == 403

    def test_wrong_mode_forbidden(self, client):
        response = client.get(
            WEBHOOK_URL,
            {
                "hub.mode": "unsubscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-123",
            },
        )
        assert response.status_code == 403

    def test_unconfigured_token_never_verifies(self, client, settings):
        """An empty configured token must not match an empty incoming token."""
        settings.META_INBOX_WEBHOOK_VERIFY_TOKEN = ""
        response = client.get(
            WEBHOOK_URL,
            {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "x"},
        )
        assert response.status_code == 403


class TestWebhookSignature:
    def test_missing_signature_unauthorized(self, client):
        response = post_payload(client, {"object": "instagram"}, signature=False)
        assert response.status_code == 401

    def test_wrong_signature_unauthorized(self, client):
        response = post_payload(client, {"object": "instagram"}, signature="sha256=deadbeef")
        assert response.status_code == 401

    def test_lead_ads_app_secret_is_rejected(self, client):
        """
        No fallback to META_CLIENT_SECRET. Sharing the secret across apps would
        let anyone holding the Lead Ads secret forge messages into any tenant.
        """
        response = post_payload(client, {"object": "instagram"}, secret=LEADADS_SECRET)
        assert response.status_code == 401

    def test_valid_signature_accepted(self, client):
        response = post_payload(client, {"object": "instagram", "entry": []})
        assert response.status_code == 200

    def test_unset_secret_rejects_everything(self, client, settings):
        settings.META_INBOX_CLIENT_SECRET = ""
        response = post_payload(client, {"object": "instagram"}, secret="")
        assert response.status_code == 401


class TestAlwaysReturns200:
    def test_malformed_json_still_200(self, client):
        body = b"{not json"
        response = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sign(body, INBOX_SECRET),
        )
        # A 5xx here would make Meta retry and eventually disable the webhook
        # for every tenant.
        assert response.status_code == 200

    def test_unknown_object_ignored(self, client):
        response = post_payload(client, {"object": "whatsapp_business_account", "entry": []})
        assert response.status_code == 200

    def test_processing_exception_still_200(self, client, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr("integrations.meta_inbox_webhook.process_entry", boom)
        response = post_payload(
            client, {"object": "instagram", "entry": [{"id": "1", "messaging": []}]}
        )
        assert response.status_code == 200


class TestIpAllowList:
    def test_disallowed_ip_forbidden(self, client, settings):
        settings.META_INBOX_WEBHOOK_ALLOWED_IPS = ["9.9.9.9"]
        response = post_payload(client, {"object": "instagram", "entry": []})
        assert response.status_code == 403

    def test_allowed_ip_passes(self, client, settings):
        settings.META_INBOX_WEBHOOK_ALLOWED_IPS = ["127.0.0.1"]
        response = post_payload(client, {"object": "instagram", "entry": []})
        assert response.status_code == 200
