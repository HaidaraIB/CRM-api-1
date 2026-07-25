"""
Tests for Mujeb inbound lead API.
"""
import json

import pytest
from rest_framework import status

from conftest import api_body
from integrations.lead_api_keys import generate_lead_api_key
from integrations.models import CompanyLeadApiKey, IntegrationAccount, IntegrationPlatform


def _auth_headers(full_key: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {full_key}"}


@pytest.fixture
def lead_api_key(company, admin_user):
    full_key, prefix, suffix, key_hash = generate_lead_api_key()
    CompanyLeadApiKey.objects.create(
        company=company,
        name="Mujeb test",
        key_prefix=prefix,
        key_suffix=suffix,
        key_hash=key_hash,
        created_by=admin_user,
        is_active=True,
    )
    return full_key


@pytest.mark.django_db
class TestMujebInboundLeadAPI:
    def test_create_lead_source_mujeb(self, api_client, company, lead_api_key):
        payload = {
            "name": "Mujeb User",
            "phone": "+9647700000099",
            "external_id": "mujeb-sub-001",
            "email": "mujeb@example.com",
        }
        response = api_client.post(
            "/api/v1/integrations/leads/mujeb/",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = api_body(response)
        assert data["client_id"]
        assert data["duplicate"] is False

        from crm.models import Client

        client = Client.objects.get(id=data["client_id"])
        assert client.company_id == company.id
        assert client.source == "mujeb"
        assert client.external_lead_id == "mujeb-sub-001"
        assert client.integration_account.platform == IntegrationPlatform.MUJEB

        account = IntegrationAccount.objects.get(
            company=company,
            platform=IntegrationPlatform.MUJEB,
            external_account_id=f"mujeb_{company.id}",
        )
        assert account.status == "connected"

    def test_missing_api_key(self, api_client):
        response = api_client.post(
            "/api/v1/integrations/leads/mujeb/",
            data=json.dumps({"name": "X"}),
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_duplicate_external_id_idempotent(self, api_client, lead_api_key):
        payload = {"name": "First", "external_id": "mujeb-dup-1"}
        r1 = api_client.post(
            "/api/v1/integrations/leads/mujeb/",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert r1.status_code == status.HTTP_201_CREATED
        id1 = api_body(r1)["client_id"]

        r2 = api_client.post(
            "/api/v1/integrations/leads/mujeb/",
            data=json.dumps({"name": "Second", "external_id": "mujeb-dup-1"}),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert r2.status_code == status.HTTP_200_OK
        data2 = api_body(r2)
        assert data2["client_id"] == id1
        assert data2["duplicate"] is True


@pytest.mark.django_db
class TestMujebConfig:
    def test_get_config(self, authenticated_admin, company, lead_api_key):
        response = authenticated_admin.get("/api/v1/integrations/accounts/mujeb-config/")
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert "endpoint_url" in data
        assert data["endpoint_url"].endswith("/integrations/leads/mujeb/")
        assert "keys" in data
        assert len(data["keys"]) >= 1
