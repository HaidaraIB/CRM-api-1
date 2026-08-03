"""Tests for PBX extension update (PATCH)."""

import pytest

from accounts.models import User
from integrations.models import PbxSettings, UserPbxExtension


@pytest.mark.django_db
def test_patch_extension_reassign_user(api_client, company, subscription):
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
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent_a,
        extension="101",
    )

    api_client.force_authenticate(user=owner)
    response = api_client.patch(
        f"/api/integrations/pbx/extensions/{mapping.id}/",
        {
            "user_id": agent_b.id,
            "extension": "102",
        },
        format="json",
    )
    assert response.status_code == 200, response.content

    payload = response.json()
    data = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    assert data["user_id"] == agent_b.id
    assert data["extension"] == "102"

    mapping.refresh_from_db()
    assert mapping.user_id == agent_b.id
    assert mapping.extension == "102"
    assert not UserPbxExtension.objects.filter(user=agent_a).exists()


@pytest.mark.django_db
def test_patch_extension_rejected_for_non_admin(api_client, company, subscription):
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
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="101",
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
def test_delete_extension(api_client, company, subscription):
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
    )
    mapping = UserPbxExtension.objects.create(
        company=company,
        user=agent,
        extension="101",
    )
    api_client.force_authenticate(user=owner)
    response = api_client.delete(f"/api/integrations/pbx/extensions/{mapping.id}/")
    assert response.status_code == 200
    assert not UserPbxExtension.objects.filter(pk=mapping.id).exists()
