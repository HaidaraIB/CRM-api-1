"""Tests for embedded softphone config helpers."""

import pytest

from accounts.models import User
from companies.models import Company
from integrations.encryption import encrypt_token
from integrations.models import PbxSettings, UserPbxExtension
from integrations.services.softphone_config import (
    build_softphone_config,
    user_softphone_ready,
)


@pytest.mark.django_db
def test_build_softphone_config_web():
    owner = User.objects.create_user(
        username="softphone_owner",
        email="owner-softphone@example.com",
        password="test-pass-123",
    )
    company = Company.objects.create(
        name="Softphone Co",
        domain="softphone-co.example.com",
        owner=owner,
    )
    agent = User.objects.create_user(
        username="agent1",
        email="agent1@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    settings = PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token-test",
        connector_api_key="conn-key-test",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
        wss_uri="wss://pbx.example.com:8089/ws",
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="110",
        sip_password=encrypt_token("ext-secret"),
        softphone_enabled=True,
    )

    assert user_softphone_ready(settings, mapping) is True
    config = build_softphone_config(settings, mapping, platform="web")
    assert config["extension"] == "110"
    assert config["sip_password"] == "ext-secret"
    assert config["wss_uri"] == "wss://pbx.example.com:8089/ws"


@pytest.mark.django_db
def test_build_softphone_config_android_prefers_wss():
    owner = User.objects.create_user(
        username="softphone_owner3",
        email="owner3@example.com",
        password="test-pass-123",
    )
    company = Company.objects.create(
        name="Softphone Co 3",
        domain="softphone-co3.example.com",
        owner=owner,
    )
    agent = User.objects.create_user(
        username="agent3",
        email="agent3@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    settings = PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token-test3",
        connector_api_key="conn-key-test3",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
        wss_uri="wss://pbx.example.com:8089/ws",
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="110",
        sip_password=encrypt_token("ext-secret"),
        softphone_enabled=True,
    )
    config = build_softphone_config(settings, mapping, platform="android")
    assert config["transport"] == "wss"
    assert config["registrar_uri"] == "wss://pbx.example.com:8089/ws"


@pytest.mark.django_db
def test_user_softphone_not_ready_without_password():
    owner = User.objects.create_user(
        username="softphone_owner2",
        email="owner2@example.com",
        password="test-pass-123",
    )
    company = Company.objects.create(
        name="Softphone Co 2",
        domain="softphone-co2.example.com",
        owner=owner,
    )
    agent = User.objects.create_user(
        username="agent2",
        email="agent2@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    settings = PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token-test2",
        connector_api_key="conn-key-test2",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="111",
        softphone_enabled=True,
    )
    assert user_softphone_ready(settings, mapping) is False


@pytest.mark.django_db
def test_build_softphone_config_ios_uses_wss():
    owner = User.objects.create_user(
        username="softphone_owner_ios",
        email="owner-ios@example.com",
        password="test-pass-123",
    )
    company = Company.objects.create(
        name="Softphone Co iOS",
        domain="softphone-ios.example.com",
        owner=owner,
    )
    agent = User.objects.create_user(
        username="agent_ios",
        email="agentios@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    settings = PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token-ios",
        connector_api_key="conn-key-ios",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
        wss_uri="wss://pbx.example.com:8089/ws",
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="120",
        sip_password=encrypt_token("ext-secret"),
        softphone_enabled=True,
    )
    config = build_softphone_config(settings, mapping, platform="ios")
    assert config["transport"] == "wss"
    assert config["sip_uri"] == "sip:120@pbx.example.com"


@pytest.mark.django_db
def test_build_softphone_config_turn_ephemeral_credentials(settings):
    settings.PBX_TURN_SHARED_SECRET = "turn-test-secret"
    owner = User.objects.create_user(
        username="softphone_turn_owner",
        email="owner-turn@example.com",
        password="test-pass-123",
    )
    company = Company.objects.create(
        name="Softphone Turn Co",
        domain="softphone-turn.example.com",
        owner=owner,
    )
    agent = User.objects.create_user(
        username="agent_turn",
        email="agentturn@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    pbx = PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token-turn",
        connector_api_key="conn-key-turn",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
        turn_server="turn:pbx.example.com:3478",
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="130",
        sip_password=encrypt_token("ext-secret"),
        softphone_enabled=True,
    )
    config = build_softphone_config(pbx, mapping, platform="android")
    turn_entry = next(e for e in config["ice_servers"] if "turn:" in e["urls"])
    assert "username" in turn_entry
    assert "credential" in turn_entry


@pytest.mark.django_db
def test_softphone_config_api_no_extension(api_client, company, subscription):
    agent = User.objects.create_user(
        username="unmapped_agent",
        email="unmapped@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token",
        connector_api_key="conn-key",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
    )
    api_client.force_authenticate(user=agent)
    response = api_client.get("/api/integrations/pbx/softphone/config/?platform=android")
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "no_extension"


@pytest.mark.django_db
def test_softphone_config_api_disabled_user(api_client, company, subscription):
    agent = User.objects.create_user(
        username="disabled_softphone",
        email="disabled@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token2",
        connector_api_key="conn-key2",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
        wss_uri="wss://pbx.example.com:8089/ws",
    )
    UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="101",
        sip_password=encrypt_token("secret"),
        softphone_enabled=False,
    )
    api_client.force_authenticate(user=agent)
    response = api_client.get("/api/integrations/pbx/softphone/config/?platform=android")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "softphone_not_configured"


@pytest.mark.django_db
def test_extension_list_never_returns_plaintext_sip_password(api_client, company, subscription):
    owner = company.owner
    agent = User.objects.create_user(
        username="list_agent",
        email="list@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token3",
        connector_api_key="conn-key3",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
    )
    UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="101",
        sip_password=encrypt_token("secret"),
        softphone_enabled=True,
    )
    api_client.force_authenticate(user=owner)
    response = api_client.get("/api/integrations/pbx/extensions/")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows[0]["sip_password_masked"] == "••••••••••••••••"
    assert "sip_password" not in rows[0]
