"""Report history is written for every save path, and never breaks the save itself."""
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from crm.models import Client, ClientCall, ClientTask
from reports.models import ConversationReply, LeadAssignmentPeriod, LeadStatusTransition
from settings.company_defaults import seed_company_settings
from settings.models import LeadStatus


@pytest.fixture
def rcompany(company):
    seed_company_settings(company)
    return company


@pytest.fixture
def statuses(rcompany):
    rows = list(LeadStatus.objects.filter(company=rcompany, is_active=True).order_by("id"))
    return rows[0], rows[1], rows[2]


def make_lead(company, status, assignee=None, **kw):
    return Client.objects.create(
        name="L", company=company, status=status, assigned_to=assignee, type="fresh", priority="low", **kw
    )


@pytest.mark.django_db
class TestRecorder:
    def test_creation_records_initial_status_and_assignment(self, rcompany, statuses, employee_user):
        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now())
        t = LeadStatusTransition.objects.get(client=lead)
        assert t.from_status is None and t.to_status == statuses[0]
        p = LeadAssignmentPeriod.objects.get(client=lead)
        assert p.user == employee_user and p.ended_at is None

    def test_status_change_writes_one_transition_with_duration(self, rcompany, statuses, employee_user):
        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now())
        lead.status = statuses[1]
        lead._report_actor = employee_user
        lead.save()
        rows = list(LeadStatusTransition.objects.filter(client=lead).order_by("id"))
        assert len(rows) == 2
        assert rows[1].from_status == statuses[0] and rows[1].to_status == statuses[1]
        assert rows[1].changed_by == employee_user and rows[1].seconds_in_previous is not None

    def test_unrelated_save_writes_nothing(self, rcompany, statuses, employee_user):
        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now())
        lead.name = "Renamed"
        lead.save()
        assert LeadStatusTransition.objects.filter(client=lead).count() == 1
        assert LeadAssignmentPeriod.objects.filter(client=lead).count() == 1

    def test_reassignment_closes_old_period_and_opens_new(self, rcompany, statuses, employee_user, admin_user):
        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now())
        lead.assigned_to = admin_user
        lead.assigned_at = timezone.now()
        lead.save()
        periods = list(LeadAssignmentPeriod.objects.filter(client=lead).order_by("id"))
        assert [p.user_id for p in periods] == [employee_user.id, admin_user.id]
        assert periods[0].ended_at is not None and periods[1].ended_at is None

    def test_unassign_closes_period(self, rcompany, statuses, employee_user):
        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now())
        lead.assigned_to = None
        lead.save()
        assert LeadAssignmentPeriod.objects.get(client=lead).ended_at is not None

    def test_first_response_credits_only_the_assignee(self, rcompany, statuses, employee_user, admin_user):
        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now() - timedelta(minutes=10))
        LeadAssignmentPeriod.objects.filter(client=lead).update(assigned_at=timezone.now() - timedelta(minutes=10))
        ClientCall.objects.create(client=lead, created_by=admin_user)  # not the assignee
        assert LeadAssignmentPeriod.objects.get(client=lead).first_response_at is None
        ClientTask.objects.create(client=lead, created_by=employee_user)
        p = LeadAssignmentPeriod.objects.get(client=lead)
        assert p.first_response_kind == "task"
        assert 590 <= p.response_seconds <= 700
        assert p.business_response_seconds is not None
        # Only the first response is kept.
        ClientCall.objects.create(client=lead, created_by=employee_user)
        assert LeadAssignmentPeriod.objects.get(client=lead).first_response_kind == "task"

    def test_status_change_by_assignee_counts_as_response(self, rcompany, statuses, employee_user):
        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now() - timedelta(minutes=5))
        LeadAssignmentPeriod.objects.filter(client=lead).update(assigned_at=timezone.now() - timedelta(minutes=5))
        lead.status = statuses[1]
        lead._report_actor = employee_user
        lead.save()
        assert LeadAssignmentPeriod.objects.get(client=lead).first_response_kind == "status_change"

    def test_recorder_failure_never_breaks_the_save(self, rcompany, statuses, employee_user):
        with mock.patch("reports.recorder.LeadStatusTransition.objects.create", side_effect=RuntimeError("boom")):
            lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now())
        assert Client.objects.filter(pk=lead.pk).exists()

    def test_conversation_reply_recorded_only_when_inbound_was_unanswered(self, rcompany, statuses, employee_user):
        from integrations.models import LeadWhatsAppMessage as M

        lead = make_lead(rcompany, statuses[0], employee_user, assigned_at=timezone.now())
        M.objects.create(client=lead, phone_number="+1", body="hi", direction=M.DIRECTION_INBOUND)
        M.objects.create(client=lead, phone_number="+1", body="hello", direction=M.DIRECTION_OUTBOUND, created_by=employee_user)
        M.objects.create(client=lead, phone_number="+1", body="again", direction=M.DIRECTION_OUTBOUND, created_by=employee_user)
        replies = ConversationReply.objects.filter(client=lead)
        assert replies.count() == 1
        assert replies[0].channel == "whatsapp" and replies[0].replied_by == employee_user
