"""Stale-in-status auto-delete: clock, management command, serializer validation."""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status

from conftest import api_body


@pytest.mark.django_db
def test_status_change_updates_status_entered_at(company):
    from crm.models import Client
    from settings.models import LeadStatus

    s1 = LeadStatus.objects.create(
        name="S1",
        company=company,
        category="active",
        color="#111111",
    )
    s2 = LeadStatus.objects.create(
        name="S2",
        company=company,
        category="active",
        color="#222222",
    )
    client = Client.objects.create(
        name="Lead",
        company=company,
        priority="medium",
        type="fresh",
        status=s1,
    )
    t0 = client.status_entered_at
    client.status = s2
    client.save()
    client.refresh_from_db()
    assert client.status_id == s2.id
    assert client.status_entered_at > t0


@pytest.mark.django_db
def test_delete_leads_stale_in_status_removes_old_leads(company):
    from crm.models import Client
    from settings.models import LeadStatus

    st = LeadStatus.objects.create(
        name="Stale bucket",
        company=company,
        category="inactive",
        color="#333333",
        auto_delete_after_hours=1,
    )
    client = Client.objects.create(
        name="Old lead",
        company=company,
        priority="low",
        type="cold",
        status=st,
    )
    Client.objects.filter(pk=client.pk).update(
        status_entered_at=timezone.now() - timedelta(hours=3),
    )
    call_command("delete_leads_stale_in_status")
    assert not Client.objects.filter(pk=client.pk).exists()


@pytest.mark.django_db
def test_delete_leads_stale_in_status_dry_run_keeps_leads(company):
    from crm.models import Client
    from settings.models import LeadStatus

    st = LeadStatus.objects.create(
        name="Dry bucket",
        company=company,
        category="inactive",
        color="#444444",
        auto_delete_after_hours=1,
    )
    client = Client.objects.create(
        name="Keep me",
        company=company,
        priority="low",
        type="cold",
        status=st,
    )
    Client.objects.filter(pk=client.pk).update(
        status_entered_at=timezone.now() - timedelta(hours=3),
    )
    call_command("delete_leads_stale_in_status", dry_run=True)
    assert Client.objects.filter(pk=client.pk).exists()


@pytest.mark.django_db
def test_lead_status_serializer_rejects_zero_auto_delete(authenticated_admin, company):
    from settings.models import LeadStatus

    ls = LeadStatus.objects.create(
        name="Z",
        company=company,
        category="active",
        color="#555555",
    )
    response = authenticated_admin.patch(
        f"/api/v1/settings/statuses/{ls.id}/",
        {"auto_delete_after_hours": 0},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_lead_status_patch_auto_delete_ok(authenticated_admin, company):
    from settings.models import LeadStatus

    ls = LeadStatus.objects.create(
        name="Y",
        company=company,
        category="active",
        color="#666666",
    )
    response = authenticated_admin.patch(
        f"/api/v1/settings/statuses/{ls.id}/",
        {"auto_delete_after_hours": 48},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert data.get("auto_delete_after_hours") == 48
