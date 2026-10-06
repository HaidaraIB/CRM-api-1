import pytest

from integrations.models import IntegrationAccount, IntegrationPlatform

pytestmark = pytest.mark.django_db


def test_overview_reports_connected_meta_account(authenticated_admin, company):
    IntegrationAccount.objects.create(
        company=company,
        platform=IntegrationPlatform.META,
        name="Lead Ads",
        status="connected",
    )
    response = authenticated_admin.get("/api/v1/integrations/overview/")
    assert response.status_code == 200
    rows = {row["key"]: row for row in response.data["data"]}
    assert rows["meta"]["status"] == "connected"
    assert rows["meta"]["account_name"] == "Lead Ads"
    assert rows["whatsapp"]["status"] == "disconnected"
    assert "policy_enabled" in rows["meta"]
