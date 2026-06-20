"""Tests for PBX extension update (PATCH)."""

import pytest

from accounts.models import User
from integrations.encryption import encrypt_token
from integrations.models import PbxSettings, UserPbxExtension


@pytest.mark.django_db
def test_patch_extension_reassign_user_and_softphone_toggle(api_client, company, subscription):
    owner = company.owner
    agent_a = User.objects.create_user(
        username="agent_a",
        email="agenta@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    agent_b = User.objects.create_user(
        username="agent_b",
        email="agentb@example.com",
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
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent_a,
        extension="101",
        sip_password=encrypt_token("old-secret"),
        softphone_enabled=True,
    )

    api_client.force_authenticate(user=owner)
    response = api_client.patch(
        f"/api/integrations/pbx/extensions/{mapping.id}/",
        {
            "user_id": agent_b.id,
            "extension": "102",
            "softphone_enabled": False,
            "sip_password": "new-secret",
        },
        format="json",
    )
    assert response.status_code == 200, response.content

    payload = response.json()
    data = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    assert data["user_id"] == agent_b.id
    assert data["extension"] == "102"
    assert data["softphone_enabled"] is False
    assert data["sip_password_masked"] == "••••••••••••••••"

    mapping.refresh_from_db()
    assert mapping.user_id == agent_b.id
    assert mapping.extension == "102"
    assert mapping.softphone_enabled is False
    assert not UserPbxExtension.objects.filter(user=agent_a).exists()


@pytest.mark.django_db
def test_patch_extension_rejected_for_non_admin(api_client, company, subscription):
    owner = company.owner
    agent = User.objects.create_user(
        username="agent_nonadmin",
        email="agentnonadmin@example.com",
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
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="101",
        sip_password=encrypt_token("secret"),
        softphone_enabled=True,
    )

    api_client.force_authenticate(user=agent)
    response = api_client.patch(
        f"/api/integrations/pbx/extensions/{mapping.id}/",
        {"extension": "999"},
        format="json",
    )
    assert response.status_code == 403

    mapping.refresh_from_db()
    assert mapping.extension == "101"


@pytest.mark.django_db
def test_delete_extension_offboards_devices(api_client, company, subscription):
    from integrations.models import UserSoftphoneDevice

    owner = company.owner
    agent = User.objects.create_user(
        username="offboard_agent",
        email="offboard@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token-off",
        connector_api_key="conn-key-off",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="101",
        sip_password=encrypt_token("secret"),
        softphone_enabled=True,
    )
    UserSoftphoneDevice.objects.create(
        company=company,
        user=agent,
        platform="ios",
        voip_token="abc123",
    )
    api_client.force_authenticate(user=owner)
    response = api_client.delete(f"/api/integrations/pbx/extensions/{mapping.id}/")
    assert response.status_code == 200
    assert UserSoftphoneDevice.objects.filter(user=agent).count() == 0
    assert not UserPbxExtension.objects.filter(pk=mapping.id).exists()


@pytest.mark.django_db
def test_patch_extension_write_only_password_not_in_response(api_client, company, subscription):
    owner = company.owner
    agent = User.objects.create_user(
        username="pwd_agent",
        email="pwd@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    PbxSettings.objects.create(
        company=company,
        webhook_token="wh-token-pwd",
        connector_api_key="conn-key-pwd",
        is_enabled=True,
        softphone_enabled=True,
        sip_domain="pbx.example.com",
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="101",
        sip_password=encrypt_token("old-secret"),
        softphone_enabled=True,
    )
    api_client.force_authenticate(user=owner)
    response = api_client.patch(
        f"/api/integrations/pbx/extensions/{mapping.id}/",
        {"extension": "101"},
        format="json",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "sip_password" not in data
    assert data["sip_password_masked"] == "••••••••••••••••"
