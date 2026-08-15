import pytest
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone

from crm.models import Client
from notifications.models import Notification, NotificationType
from notifications.team_activity import notify_owner_team_activity
from notifications.translations import get_team_activity_text


@pytest.mark.django_db
def test_notify_owner_team_activity_skips_when_actor_is_owner(company, owner_user):
    with patch("notifications.team_activity.NotificationService.send_notification") as send_mock:
        result = notify_owner_team_activity(
            owner_user,
            company,
            action="edit",
            lead_name="L",
            detail="name updated",
        )

        assert result is False
        send_mock.assert_not_called()


@pytest.mark.django_db
def test_notify_owner_team_activity_sends_to_owner(company, employee_user):
    with patch(
        "notifications.team_activity.NotificationService.send_notification",
        return_value=True,
    ) as send_mock:
        result = notify_owner_team_activity(
            employee_user,
            company,
            action="call_logged",
            lead_id=10,
            lead_name="Test Lead",
        )

        assert result is True
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        assert kwargs["user"] == company.owner
        assert kwargs["notification_type"] == NotificationType.TEAM_ACTIVITY
        assert kwargs["sender_role"] == employee_user.role
        assert kwargs["data"]["action"] == "call_logged"
        assert kwargs["data"]["lead_id"] == 10
        assert kwargs["skip_database_insert"] is True
        assert "Employee" in kwargs["body"] or "الموظف" in kwargs["body"]
        assert Notification.objects.filter(user=company.owner, type=NotificationType.TEAM_ACTIVITY).exists()


@pytest.mark.django_db
def test_team_activity_status_change_arabic_template():
    body = get_team_activity_text(
        "ar",
        "status_change",
        employee="e1",
        lead="l1",
        old_status="s1",
        new_status="s2",
    )["body"]
    assert "e1" in body and "l1" in body and "s1" in body and "s2" in body
    assert "الموظف" in body


@pytest.mark.django_db
def test_team_activity_no_follow_up_templates():
    ar = get_team_activity_text(
        "ar", "no_follow_up", employee="Ali", lead="Lead A", hours=6
    )["body"]
    en = get_team_activity_text(
        "en", "no_follow_up", employee="Ali", lead="Lead A", hours=6
    )["body"]
    assert "Ali" in ar and "Lead A" in ar and "6" in ar
    assert "تأخر" in ar
    assert "Ali" in en and "Lead A" in en and "6" in en
    assert "overdue" in en.lower()


@pytest.mark.django_db
def test_owner_not_notified_on_generic_field_edit(
    authenticated_employee,
    company,
    employee_user,
):
    lead = Client.objects.create(
        name="Edit Me",
        company=company,
        priority="high",
        type="fresh",
        assigned_to=employee_user,
    )

    with patch(
        "crm.signals.notify_owner_team_activity",
        return_value=True,
    ) as owner_mock:
        response = authenticated_employee.patch(
            f"/api/v1/clients/{lead.id}/",
            {"name": "Edited Name", "notes": "just a note"},
            format="json",
        )
        assert response.status_code == 200, getattr(response, "data", response.content)
        assert owner_mock.call_count == 0


@pytest.mark.django_db
def test_team_activity_settings_key_mapping():
    from notifications.team_activity import team_activity_settings_key

    assert team_activity_settings_key("status_change") == "team_activity_status"
    assert team_activity_settings_key("call_logged") == "team_activity_action"
    assert team_activity_settings_key("no_follow_up") == "team_activity_overdue"


@pytest.mark.django_db
def test_team_activity_edit_detail_localizes_legacy_english_notes():
    body = get_team_activity_text(
        "ar",
        "edit",
        employee="Zainab",
        lead="Tahseen",
        detail="Type updated",
    )["body"]
    assert "النوع" in body
    assert "Type updated" not in body


@pytest.mark.django_db
def test_team_activity_assignment_localizes_unassigned():
    body = get_team_activity_text(
        "ar",
        "assignment",
        employee="Ali",
        lead="Lead A",
        old_assignee="Unassigned",
        new_assignee="Sara",
    )["body"]
    assert "غير معيّن" in body or "غير مع" in body
    assert "Unassigned" not in body


@pytest.mark.django_db
def test_employee_creating_client_call_notifies_owner(
    authenticated_employee,
    company,
    employee_user,
):
    lead = Client.objects.create(
        name="Lead For Call",
        company=company,
        priority="high",
        type="fresh",
        assigned_to=employee_user,
    )

    with patch(
        "notifications.team_activity.NotificationService.send_notification",
        return_value=True,
    ) as send_mock:
        response = authenticated_employee.post(
            "/api/v1/client-calls/",
            {"client": lead.id, "notes": "Follow-up call"},
            format="json",
        )

        assert response.status_code == 201
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        assert kwargs["user"] == company.owner
        assert kwargs["notification_type"] == NotificationType.TEAM_ACTIVITY
        assert kwargs["data"]["action"] == "call_logged"
        assert kwargs["data"]["lead_id"] == lead.id
        assert kwargs.get("skip_database_insert") is True
        assert Notification.objects.filter(user=company.owner, type=NotificationType.TEAM_ACTIVITY).exists()


@pytest.mark.django_db
def test_employee_creating_client_sends_new_lead_to_owner_not_team_activity(
    authenticated_employee,
    company,
    employee_user,
):
    with patch(
        "notifications.services.NotificationService.send_notification",
        return_value=True,
    ) as send_mock:
        response = authenticated_employee.post(
            "/api/v1/clients/",
            {
                "name": "Manual New Lead",
                "priority": "medium",
                "type": "fresh",
                "company": company.id,
            },
            format="json",
        )

        assert response.status_code == 201, getattr(response, "data", response.content)

        new_lead_calls = [
            c
            for c in send_mock.call_args_list
            if c.kwargs.get("notification_type") == NotificationType.NEW_LEAD
        ]
        assert len(new_lead_calls) == 1, send_mock.call_args_list
        kwargs = new_lead_calls[0].kwargs
        assert kwargs["user"] == company.owner
        assert kwargs["data"]["lead_name"] == "Manual New Lead"
        assert "added_by" in kwargs["data"]
        assert kwargs["data"]["added_by"]
        expected_name = (employee_user.get_full_name() or employee_user.username).strip()
        assert kwargs["data"]["added_by"] == expected_name

        team_activity_calls = [
            c
            for c in send_mock.call_args_list
            if c.kwargs.get("notification_type") == NotificationType.TEAM_ACTIVITY
        ]
        assert team_activity_calls == []
        assert not Notification.objects.filter(
            user=company.owner, type=NotificationType.TEAM_ACTIVITY
        ).exists()


@pytest.mark.django_db
def test_check_lead_no_follow_up_notifies_assignee_and_owner_digest(
    company,
    employee_user,
    subscription,
):
    """
    The owner now receives one aggregate digest rather than a per-lead team_activity push
    (which is what flooded owners); the assignee still gets the per-lead alert.
    """
    company.no_follow_up_hours = 10
    company.no_follow_up_digest_hour = timezone.now().hour
    company.save(update_fields=["no_follow_up_hours", "no_follow_up_digest_hour"])

    lead = Client.objects.create(
        name="Stale Lead",
        company=company,
        priority="high",
        type="fresh",
        assigned_to=employee_user,
        assigned_at=timezone.now() - timedelta(hours=10),
        last_contacted_at=timezone.now() - timedelta(hours=10),
    )

    with patch(
        "notifications.management.commands.check_lead_no_follow_up.NotificationService.send_notification",
        return_value=True,
    ) as assignee_mock, patch(
        "notifications.management.commands.check_lead_no_follow_up.notify_owner_team_activity",
        return_value=True,
    ) as owner_mock:
        call_command("check_lead_no_follow_up")

        assignee_mock.assert_called_once()
        assignee_kwargs = assignee_mock.call_args.kwargs
        assert assignee_kwargs["user"] == employee_user
        assert assignee_kwargs["notification_type"] == NotificationType.LEAD_NO_FOLLOW_UP
        assert assignee_kwargs["data"]["lead_id"] == lead.id
        assert assignee_kwargs["data"]["hours"] == 10

        owner_mock.assert_called_once()
        owner_args, owner_kwargs = owner_mock.call_args
        assert owner_args[0] is None  # aggregate digest has no single actor
        assert owner_args[1] == company
        assert owner_kwargs["action"] == "no_follow_up_digest"
        assert owner_kwargs["count"] == 1
        assert owner_kwargs["employee_count"] == 1


@pytest.mark.django_db
def test_team_activity_no_follow_up_digest_templates():
    ar = get_team_activity_text(
        "ar", "no_follow_up_digest", count=5, employee_count=3
    )["body"]
    en = get_team_activity_text(
        "en", "no_follow_up_digest", count=5, employee_count=3
    )["body"]
    assert "5" in ar and "3" in ar
    assert "متابعة" in ar
    assert "5" in en and "3" in en
    assert "follow-up" in en
