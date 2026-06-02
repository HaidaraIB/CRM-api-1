"""Tests for backfill_lead_default_status management command."""
import pytest
from django.core.management import call_command

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


@pytest.mark.django_db
def test_backfill_sets_default_status(company, default_lead_status):
    client = Client.objects.create(
        name="Meta Lead",
        company=company,
        priority="medium",
        type="fresh",
        source="meta_lead_form",
    )
    Client.objects.filter(pk=client.pk).update(status_id=None)

    call_command("backfill_lead_default_status")

    client.refresh_from_db()
    assert client.status_id == default_lead_status.id
    assert client.status_entered_at == client.created_at


@pytest.mark.django_db
def test_backfill_dry_run_does_not_update(company, default_lead_status):
    client = Client.objects.create(
        name="No Status",
        company=company,
        priority="medium",
        type="fresh",
        source="meta_lead_form",
    )
    Client.objects.filter(pk=client.pk).update(status_id=None)

    call_command("backfill_lead_default_status", dry_run=True)

    client.refresh_from_db()
    assert client.status_id is None
