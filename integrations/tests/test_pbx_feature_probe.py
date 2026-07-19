"""PBX feature-detection GETs should not 403 when plan/policy blocks PBX."""

import pytest

from accounts.models import User
from crm_saas_api.responses import error_response


def _gated_response(*_args, **_kwargs):
    return error_response(
        "This integration is not included in your current plan.",
        code="plan_integration_not_included",
        status_code=403,
    )


@pytest.mark.django_db
def test_pbx_settings_get_returns_disabled_stub_when_gated(api_client, company, subscription, monkeypatch):
    monkeypatch.setattr("integrations.views.pbx._integration_gate", _gated_response)

    api_client.force_authenticate(user=company.owner)
    response = api_client.get("/api/integrations/pbx/settings/")
    assert response.status_code == 200, response.content
    payload = response.json()
    data = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    assert data["is_enabled"] is False
    assert data["softphone_enabled"] is False
    assert data["screen_pop_enabled"] is False


@pytest.mark.django_db
def test_pbx_settings_put_still_forbidden_when_gated(api_client, company, subscription, monkeypatch):
    monkeypatch.setattr("integrations.views.pbx._integration_gate", _gated_response)

    api_client.force_authenticate(user=company.owner)
    response = api_client.put(
        "/api/integrations/pbx/settings/",
        {"is_enabled": True},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_pbx_extensions_get_returns_empty_when_gated(api_client, company, subscription, monkeypatch):
    agent = User.objects.create_user(
        username="pbx_probe_agent",
        email="pbx_probe_agent@example.com",
        password="test-pass-123",
        company=company,
        role="employee",
    )
    monkeypatch.setattr("integrations.views.pbx._integration_gate", _gated_response)

    api_client.force_authenticate(user=agent)
    response = api_client.get("/api/integrations/pbx/extensions/")
    assert response.status_code == 200, response.content
    payload = response.json()
    data = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    assert data == []
