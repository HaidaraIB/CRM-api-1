"""Tests for default lead status assignment on create."""
import pytest

from crm.models import Client
from settings.models import LeadStatus


@pytest.fixture
def default_lead_status(company):
    return LeadStatus.objects.create(
        company=company,
        name="New Lead",
        is_default=True,
        is_active=True,
        is_hidden=False,
    )


@pytest.mark.parametrize(
    "source",
    ["meta_lead_form", "whatsapp", "tiktok", "manual", "api"],
)
def test_new_lead_gets_default_status(company, default_lead_status, source):
    client = Client.objects.create(
        name="Integration Lead",
        company=company,
        priority="medium",
        type="fresh",
        source=source,
    )
    assert client.status_id == default_lead_status.id


def test_explicit_status_is_not_overridden(company, default_lead_status):
    other_status = LeadStatus.objects.create(
        company=company,
        name="Contacted Explicit",
        is_default=False,
        is_active=True,
        is_hidden=False,
    )
    client = Client.objects.create(
        name="Explicit Status Lead",
        company=company,
        priority="medium",
        type="fresh",
        source="meta_lead_form",
        status=other_status,
    )
    assert client.status_id == other_status.id
