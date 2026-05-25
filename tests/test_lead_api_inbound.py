"""
Tests for Custom Lead API (inbound + key management).
"""
import json

import pytest
from rest_framework import status

from conftest import api_body
from integrations.lead_api_keys import generate_lead_api_key
from integrations.models import CompanyLeadApiKey


def _auth_headers(full_key: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {full_key}"}


@pytest.fixture
def lead_api_key(company, admin_user):
    full_key, prefix, suffix, key_hash = generate_lead_api_key()
    CompanyLeadApiKey.objects.create(
        company=company,
        name="Test form",
        key_prefix=prefix,
        key_suffix=suffix,
        key_hash=key_hash,
        created_by=admin_user,
        is_active=True,
    )
    return full_key


@pytest.mark.django_db
class TestInboundLeadAPI:
    def test_create_lead_success(self, api_client, company, lead_api_key):
        payload = {
            "name": "Jane Doe",
            "phone": "+9647700000001",
            "external_id": "sub-001",
            "email": "jane@example.com",
        }
        response = api_client.post(
            "/api/v1/integrations/leads/inbound/",
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
        assert client.source == "api"
        assert client.external_lead_id == "sub-001"
        assert client.name == "Jane Doe"

    def test_create_lead_assigns_default_status(self, api_client, company, lead_api_key):
        from settings.models import LeadStatus

        default_status = LeadStatus.objects.create(
            company=company,
            name="New",
            is_default=True,
            is_active=True,
            is_hidden=False,
        )
        payload = {"name": "Status Test", "external_id": "status-default-001"}
        response = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert response.status_code == 201
        from crm.models import Client

        client = Client.objects.get(id=api_body(response)["client_id"])
        assert client.status_id == default_status.id

    def test_missing_api_key(self, api_client):
        response = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps({"name": "X"}),
            content_type="application/json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_api_key(self, api_client):
        response = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps({"name": "X"}),
            content_type="application/json",
            **_auth_headers("crm_lk_invalid_key_xxx"),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_duplicate_external_id_idempotent(self, api_client, company, lead_api_key):
        payload = {"name": "First", "external_id": "dup-1"}
        r1 = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert r1.status_code == status.HTTP_201_CREATED
        id1 = api_body(r1)["client_id"]

        r2 = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps({"name": "Second", "external_id": "dup-1"}),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert r2.status_code == status.HTTP_200_OK
        data2 = api_body(r2)
        assert data2["client_id"] == id1
        assert data2["duplicate"] is True

    def test_inactive_key_forbidden(self, api_client, company, admin_user):
        full_key, prefix, suffix, key_hash = generate_lead_api_key()
        row = CompanyLeadApiKey.objects.create(
            company=company,
            name="Revoked",
            key_prefix=prefix,
            key_suffix=suffix,
            key_hash=key_hash,
            created_by=admin_user,
            is_active=False,
        )
        assert row.is_active is False
        response = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps({"name": "X"}),
            content_type="application/json",
            **_auth_headers(full_key),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_validation_requires_name(self, api_client, lead_api_key):
        response = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps({"phone": "123"}),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_campaign_must_belong_to_company(
        self, api_client, company, other_company, lead_api_key
    ):
        from crm.models import Campaign

        other_campaign = Campaign.objects.create(
            code="OTHER01",
            name="Other",
            company=other_company,
        )
        response = api_client.post(
            "/api/v1/integrations/leads/inbound/",
            data=json.dumps({"name": "X", "campaign_id": other_campaign.id}),
            content_type="application/json",
            **_auth_headers(lead_api_key),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestLeadApiKeyManagement:
    def test_get_config(self, authenticated_admin, company, lead_api_key):
        response = authenticated_admin.get("/api/v1/integrations/accounts/lead-api-config/")
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert "endpoint_url" in data
        assert "keys" in data
        assert len(data["keys"]) >= 1

    def test_create_key_admin_only(self, authenticated_admin):
        response = authenticated_admin.post(
            "/api/v1/integrations/accounts/lead-api-keys/",
            {"name": "Website"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = api_body(response)
        assert data.get("api_key", "").startswith("crm_lk_")
        assert "key_prefix" in data

    def test_create_key_denied_for_employee(self, authenticated_employee):
        response = authenticated_employee.post(
            "/api/v1/integrations/accounts/lead-api-keys/",
            {"name": "Website"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_revoke_key(self, authenticated_admin, company, admin_user):
        full_key, prefix, suffix, key_hash = generate_lead_api_key()
        row = CompanyLeadApiKey.objects.create(
            company=company,
            name="To revoke",
            key_prefix=prefix,
            key_suffix=suffix,
            key_hash=key_hash,
            created_by=admin_user,
        )
        response = authenticated_admin.delete(
            f"/api/v1/integrations/accounts/lead-api-keys/{row.id}/",
        )
        assert response.status_code == status.HTTP_200_OK
        row.refresh_from_db()
        assert row.is_active is False
