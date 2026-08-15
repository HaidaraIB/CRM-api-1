"""
Per-lead scheduling for the "no follow-up" alerts.

The behaviour under test is the fix for owners being flooded: instead of a fixed 6-hourly
sweep that re-notified every overdue lead at once (and repeated forever when the assignee
had the notification muted), each lead is alerted on its own clock, exactly once per
escalation rung, with the owner receiving a single daily digest.
"""
import pytest
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone

from crm.models import Client
from notifications.models import (
    Notification,
    NotificationSettings,
    NotificationType,
    ReminderDispatchLog,
)
from settings.models import LeadStatus, StatusCategory


SEND_PATH = "notifications.management.commands.check_lead_no_follow_up.NotificationService.send_notification"
DIGEST_PATH = "notifications.management.commands.check_lead_no_follow_up.notify_owner_team_activity"


def make_lead(company, employee, *, hours_idle, name="Stale Lead", status=None):
    reference = timezone.now() - timedelta(hours=hours_idle)
    return Client.objects.create(
        name=name,
        company=company,
        priority="high",
        type="fresh",
        status=status,
        assigned_to=employee,
        assigned_at=reference,
        last_contacted_at=reference,
    )


def set_last_contacted(lead, when):
    """
    Move a lead's clock without going through save().

    crm.signals.notify_lead_updated resets last_contacted_at to now on *any* Client.save(),
    so a plain save() here would silently undo the setup.
    """
    Client.objects.filter(pk=lead.pk).update(last_contacted_at=when)
    lead.refresh_from_db(fields=["last_contacted_at"])


def run(**kwargs):
    """Run the command with both notification sinks mocked; return (assignee, digest)."""
    with patch(SEND_PATH, return_value=True) as assignee_mock, patch(
        DIGEST_PATH, return_value=True
    ) as digest_mock:
        call_command("check_lead_no_follow_up", **kwargs)
        return assignee_mock, digest_mock


@pytest.mark.django_db
def test_lead_below_sla_is_not_notified(company, employee_user, subscription):
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    make_lead(company, employee_user, hours_idle=9)

    assignee_mock, _ = run()

    assignee_mock.assert_not_called()


@pytest.mark.django_db
def test_lead_at_sla_is_notified_once(company, employee_user, subscription):
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    lead = make_lead(company, employee_user, hours_idle=10)

    assignee_mock, _ = run()

    assignee_mock.assert_called_once()
    kwargs = assignee_mock.call_args.kwargs
    assert kwargs["user"] == employee_user
    assert kwargs["notification_type"] == NotificationType.LEAD_NO_FOLLOW_UP
    assert kwargs["data"]["lead_id"] == lead.id
    # hours is an exact multiple of the SLA, not "elapsed since contact"
    assert kwargs["data"]["hours"] == 10
    assert kwargs["data"]["escalation_step"] == 1


@pytest.mark.django_db
def test_repeated_runs_do_not_repeat_the_notification(company, employee_user, subscription):
    """The core regression: the old command re-fired on every sweep."""
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    make_lead(company, employee_user, hours_idle=10)

    total = 0
    for _ in range(5):
        assignee_mock, _ = run()
        total += assignee_mock.call_count

    assert total == 1


@pytest.mark.django_db
def test_escalation_ladder_caps_at_three(company, employee_user, subscription):
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])

    lead = make_lead(company, employee_user, hours_idle=0)
    reference = timezone.now()
    set_last_contacted(lead, reference)

    seen = []
    # The lead is never contacted again; only the clock moves forward.
    for multiple in (1, 2, 3, 4):
        fake_now = reference + timedelta(hours=10 * multiple, minutes=1)
        with patch(
            "notifications.management.commands.check_lead_no_follow_up.timezone.now",
            return_value=fake_now,
        ):
            assignee_mock, _ = run()
        seen.extend(
            c.kwargs["data"]["escalation_step"] for c in assignee_mock.call_args_list
        )

    # Three rungs fire, then the lead goes quiet — the 4th period adds nothing.
    assert seen == [1, 2, 3]


@pytest.mark.django_db
def test_lead_first_seen_past_the_last_rung_still_alerts_once(
    company, employee_user, subscription
):
    """
    A backlog lead (or one uncovered after a cron outage) is already past 3x the SLA.
    It must still get its final alert rather than being silently skipped — and only one.
    """
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    lead = make_lead(company, employee_user, hours_idle=0)
    set_last_contacted(lead, timezone.now() - timedelta(hours=200))

    first, _ = run()
    second, _ = run()

    assert first.call_count == 1
    assert first.call_args.kwargs["data"]["escalation_step"] == 3
    assert second.call_count == 0


@pytest.mark.django_db
def test_ladder_rearms_after_real_contact(company, employee_user, subscription):
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    lead = make_lead(company, employee_user, hours_idle=10)

    assignee_mock, _ = run()
    assert assignee_mock.call_count == 1

    # The employee follows up; the clock resets and the lead goes quiet...
    set_last_contacted(lead, timezone.now())
    assignee_mock, _ = run()
    assert assignee_mock.call_count == 0

    # ...until it is idle for a full SLA again, when it fires afresh.
    set_last_contacted(lead, timezone.now() - timedelta(hours=10))
    assignee_mock, _ = run()
    assert assignee_mock.call_count == 1


@pytest.mark.django_db
def test_muted_assignee_still_caps_the_ladder(company, employee_user, subscription):
    """
    The bug behind the flood: the old cap counted the assignee's inbox rows, but a muted
    assignee never gets one, so the counter never advanced and the owner was notified
    forever. The dispatch log is written regardless of preferences.
    """
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    settings_obj = NotificationSettings.get_or_create_for_user(employee_user)
    settings_obj.notification_types = {NotificationType.LEAD_NO_FOLLOW_UP: False}
    settings_obj.save(update_fields=["notification_types"])

    lead = make_lead(company, employee_user, hours_idle=10)

    for _ in range(5):
        call_command("check_lead_no_follow_up")

    # No inbox row (correctly muted), but exactly one claim — so no unbounded retry.
    assert not Notification.objects.filter(
        user=employee_user, type=NotificationType.LEAD_NO_FOLLOW_UP
    ).exists()
    assert (
        ReminderDispatchLog.objects.filter(
            user=employee_user,
            notification_type=NotificationType.LEAD_NO_FOLLOW_UP,
            object_id=str(lead.id),
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_closed_status_leads_are_skipped(company, employee_user, subscription):
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    closed = LeadStatus.objects.create(
        name="Won", company=company, category=StatusCategory.CLOSED.value
    )
    make_lead(company, employee_user, hours_idle=48, status=closed)

    assignee_mock, _ = run()

    assignee_mock.assert_not_called()


@pytest.mark.django_db
def test_company_without_active_subscription_is_skipped(company, employee_user):
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    make_lead(company, employee_user, hours_idle=48)

    assignee_mock, _ = run()

    assignee_mock.assert_not_called()


@pytest.mark.django_db
def test_disabled_company_setting_is_skipped(company, employee_user, subscription):
    company.no_follow_up_enabled = False
    company.save(update_fields=["no_follow_up_enabled"])
    make_lead(company, employee_user, hours_idle=48)

    assignee_mock, _ = run()

    assignee_mock.assert_not_called()


@pytest.mark.django_db
def test_dry_run_writes_no_dispatch_rows(company, employee_user, subscription):
    company.no_follow_up_hours = 10
    company.save(update_fields=["no_follow_up_hours"])
    make_lead(company, employee_user, hours_idle=10)

    assignee_mock, _ = run(dry_run=True)

    assignee_mock.assert_not_called()
    assert ReminderDispatchLog.objects.count() == 0


@pytest.mark.django_db
def test_owner_receives_one_digest_per_local_day(company, employee_user, subscription):
    """The owner gets a single aggregate push, not one per overdue lead."""
    company.no_follow_up_hours = 10
    company.timezone = "UTC"
    company.no_follow_up_digest_hour = timezone.now().hour
    company.save(
        update_fields=["no_follow_up_hours", "timezone", "no_follow_up_digest_hour"]
    )

    for i in range(3):
        make_lead(company, employee_user, hours_idle=10, name=f"Lead {i}")

    _, digest_mock = run()
    assert digest_mock.call_count == 1
    kwargs = digest_mock.call_args.kwargs
    assert kwargs["action"] == "no_follow_up_digest"
    assert kwargs["count"] == 3
    assert kwargs["employee_count"] == 1

    # Re-running the same local day must not send a second digest.
    _, digest_mock = run()
    assert digest_mock.call_count == 0


@pytest.mark.django_db
def test_digest_waits_for_the_company_local_hour(company, employee_user, subscription):
    company.no_follow_up_hours = 10
    company.timezone = "UTC"
    # Pick an hour that has not arrived yet today.
    company.no_follow_up_digest_hour = (timezone.now().hour + 2) % 24
    company.save(
        update_fields=["no_follow_up_hours", "timezone", "no_follow_up_digest_hour"]
    )
    make_lead(company, employee_user, hours_idle=10)

    assignee_mock, digest_mock = run()

    # The per-lead alert still fires immediately; only the digest waits for its slot.
    assert assignee_mock.call_count == 1
    if company.no_follow_up_digest_hour > timezone.now().hour:
        assert digest_mock.call_count == 0
