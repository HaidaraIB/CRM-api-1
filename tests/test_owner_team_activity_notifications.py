import pytest
from unittest.mock import patch

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
