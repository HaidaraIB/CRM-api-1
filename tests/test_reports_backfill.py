"""Backfill rebuilds history from legacy ClientEvent rows, idempotently, without touching live rows."""
from datetime import timedelta

import pytest
from django.utils import timezone

from crm.models import Client, ClientCall, ClientEvent
from reports.backfill import rebuild_company
from reports.models import LeadAssignmentPeriod, LeadStatusTransition
from settings.company_defaults import seed_company_settings
from settings.models import LeadStatus


@pytest.fixture
def legacy(company, employee_user, admin_user):
    seed_company_settings(company)
    s = list(LeadStatus.objects.filter(company=company, is_active=True).order_by("id"))
    t0 = timezone.now() - timedelta(days=3)
    lead = Client.objects.create(
        name="Legacy", company=company, status=s[1], assigned_to=employee_user, assigned_at=t0, type="fresh", priority="low"
    )
    Client.objects.filter(pk=lead.pk).update(created_at=t0)
    lead.refresh_from_db()
    # Pretend this lead predates the recorder: drop live rows, keep only legacy events.
    LeadStatusTransition.objects.filter(client=lead).delete()
    LeadAssignmentPeriod.objects.filter(client=lead).delete()
    ev = []
    ev.append(ClientEvent.objects.create(client=lead, event_type="assignment", old_value="Unassigned",
                                         new_value=employee_user.get_full_name() or employee_user.username, created_by=admin_user))
    ev.append(ClientEvent.objects.create(client=lead, event_type="status_change", old_value=s[0].name,
                                         new_value=s[1].name, created_by=employee_user, reason="called"))
    ev.append(ClientEvent.objects.create(client=lead, event_type="status_change", old_value=s[1].name,
                                         new_value="Deleted Status", created_by=employee_user))
    for e, d in zip(ev, (timedelta(hours=1), timedelta(hours=3), timedelta(hours=5))):
        ClientEvent.objects.filter(pk=e.pk).update(created_at=t0 + d)
    call = ClientCall.objects.create(client=lead, created_by=employee_user)
    ClientCall.objects.filter(pk=call.pk).update(created_at=t0 + timedelta(hours=2))
    LeadAssignmentPeriod.objects.filter(client=lead).delete()  # call signal may have no open period; keep clean
    return lead, s


@pytest.mark.django_db
def test_rebuild_reconstructs_path_with_name_snapshots(company, legacy, employee_user):
    lead, s = legacy
    totals = rebuild_company(company)
    assert totals["transitions"] == 3
    rows = list(LeadStatusTransition.objects.filter(client=lead).order_by("changed_at", "id"))
    assert [r.to_status_name for r in rows] == [s[0].name, s[1].name, "Deleted Status"]
    assert rows[0].from_status is None and rows[0].to_status == s[0]
    assert rows[2].to_status is None  # unmatched name keeps its snapshot only
    assert rows[1].changed_by == employee_user and rows[1].reason == "called"
    assert all(r.is_backfilled for r in rows)


@pytest.mark.django_db
def test_rebuild_computes_assignment_period_and_first_response(company, legacy, employee_user):
    lead, _ = legacy
    rebuild_company(company)
    p = LeadAssignmentPeriod.objects.get(client=lead)
    assert p.user == employee_user and p.is_backfilled and p.ended_at is None
    assert p.first_response_kind == "call"
    assert p.response_seconds == 3600  # assigned +1h, call +2h


@pytest.mark.django_db
def test_rebuild_is_idempotent(company, legacy):
    rebuild_company(company)
    first = (LeadStatusTransition.objects.count(), LeadAssignmentPeriod.objects.count())
    rebuild_company(company)
    assert (LeadStatusTransition.objects.count(), LeadAssignmentPeriod.objects.count()) == first


@pytest.mark.django_db
def test_rebuild_does_not_touch_live_rows(company, employee_user):
    seed_company_settings(company)
    s = list(LeadStatus.objects.filter(company=company, is_active=True).order_by("id"))
    lead = Client.objects.create(name="Live", company=company, status=s[0], assigned_to=employee_user,
                                 assigned_at=timezone.now(), type="fresh", priority="low")
    live_ids = set(LeadStatusTransition.objects.filter(client=lead).values_list("id", flat=True))
    rebuild_company(company)
    assert live_ids <= set(LeadStatusTransition.objects.filter(client=lead).values_list("id", flat=True))
    assert LeadAssignmentPeriod.objects.filter(client=lead, ended_at__isnull=True).count() == 1
