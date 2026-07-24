"""
Assignee inbox notifications must persist independently of FCM push delivery.
"""
from unittest.mock import patch

import pytest
from django.utils import timezone

from crm.models import Client
from crm.signals import notify_lead_assignment_change
from notifications.models import Notification, NotificationSettings, NotificationType
from notifications.services import NotificationService
from settings.models import LeadStatus


@pytest.fixture
def lead_statuses(company, db):
    s1, _ = LeadStatus.objects.get_or_create(
        company=company,
        name="AssignNotif New",
        defaults={"is_active": True, "is_default": True},
    )
    s2, _ = LeadStatus.objects.get_or_create(
        company=company,
        name="AssignNotif Contacted",
        defaults={"is_active": True},
    )
    return s1, s2


@pytest.mark.django_db
def test_assign_via_save_creates_assignee_inbox_without_fcm_token(
    company, employee_user, lead_statuses,
):
    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Assign Me",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=None,
    )
    assert not employee_user.iter_fcm_tokens_for_push()

    lead.assigned_to = employee_user
    lead.assigned_at = timezone.now()
    lead.save()

    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_bulk_assign_creates_assignee_inbox(
    authenticated_admin, company, employee_user, lead_statuses, subscription,
):
    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Bulk Assign Me",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=None,
    )

    response = authenticated_admin.post(
        "/api/v1/clients/bulk_assign/",
        {"client_ids": [lead.id], "user_id": employee_user.id},
        format="json",
    )
    assert response.status_code == 200, getattr(response, "data", response.content)

    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_status_change_creates_assignee_inbox(
    company, employee_user, lead_statuses,
):
    status_new, status_contacted = lead_statuses
    lead = Client.objects.create(
        name="Status Change Me",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=employee_user,
    )
    Notification.objects.filter(user=employee_user).delete()

    lead.status = status_contacted
    lead.save()

    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_STATUS_CHANGED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_quiet_hours_still_persist_inbox_row(company, employee_user):
    """Quiet hours must skip push only — never block the Notification row."""
    settings_obj = NotificationSettings.get_or_create_for_user(employee_user)
    settings_obj.restrict_time = True
    # Impossible window so can_send_now() is always False.
    settings_obj.start_hour = 3
    settings_obj.end_hour = 3
    settings_obj.enabled_days = [True] * 7
    settings_obj.save()

    with patch.object(NotificationService, "initialize", return_value=True):
        with patch.object(employee_user, "iter_fcm_tokens_for_push", return_value=["fake-token"]):
            with patch("notifications.services.messaging") as messaging_mock:
                result = NotificationService.send_notification(
                    user=employee_user,
                    notification_type=NotificationType.LEAD_ASSIGNED,
                    data={"lead_id": 1, "lead_name": "Quiet Hours Lead"},
                )

    assert result is False
    messaging_mock.send.assert_not_called()
    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_fcm_failure_still_persists_inbox_row(company, employee_user):
    with patch.object(NotificationService, "initialize", return_value=True):
        with patch.object(employee_user, "iter_fcm_tokens_for_push", return_value=["fake-token"]):
            with patch("notifications.services.messaging") as messaging_mock:
                messaging_mock.Notification = lambda **kwargs: object()
                messaging_mock.Message = lambda **kwargs: object()
                messaging_mock.AndroidConfig = lambda **kwargs: object()
                messaging_mock.AndroidNotification = lambda **kwargs: object()
                messaging_mock.APNSConfig = lambda **kwargs: object()
                messaging_mock.APNSPayload = lambda **kwargs: object()
                messaging_mock.Aps = lambda **kwargs: object()
                messaging_mock.send.side_effect = RuntimeError("FCM down")
                # UnregisteredError used in except — provide a dummy type
                messaging_mock.UnregisteredError = type("UnregisteredError", (Exception,), {})

                result = NotificationService.send_notification(
                    user=employee_user,
                    notification_type=NotificationType.LEAD_ASSIGNED,
                    data={"lead_id": 2, "lead_name": "FCM Fail Lead"},
                )

    assert result is False
    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
        data__lead_name="FCM Fail Lead",
    ).exists()


@pytest.mark.django_db
def test_no_fcm_token_still_persists_inbox_row(company, employee_user):
    with patch.object(NotificationService, "initialize", return_value=True):
        result = NotificationService.send_notification(
            user=employee_user,
            notification_type=NotificationType.LEAD_STATUS_CHANGED,
            data={"lead_id": 3, "lead_name": "No Token", "new_status": "Contacted"},
        )

    assert result is False
    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_STATUS_CHANGED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_notify_lead_assignment_change_helper_creates_row(company, employee_user, lead_statuses):
    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Helper Assign",
        company=company,
        priority="low",
        type="fresh",
        status=status_new,
        assigned_to=None,
    )
    notify_lead_assignment_change(
        client=lead,
        old_assignee=None,
        new_assignee=employee_user,
    )
    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
    ).exists()


@pytest.mark.django_db
def test_actor_reassign_from_self_skips_transferred(
    company, owner_user, employee_user, lead_statuses,
):
    """Owner reassigning a lead they held must not get lead_transferred."""
    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Owner Held Lead",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=owner_user,
    )
    Notification.objects.filter(user__in=[owner_user, employee_user]).delete()

    lead.assigned_to = employee_user
    lead.assigned_at = timezone.now()
    lead._notification_actor = owner_user
    lead.save()

    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()
    assert not Notification.objects.filter(
        user=owner_user,
        type=NotificationType.LEAD_TRANSFERRED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_actor_assign_to_self_skips_lead_assigned(company, owner_user, lead_statuses):
    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Self Assign Lead",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=None,
    )
    Notification.objects.filter(user=owner_user).delete()

    notify_lead_assignment_change(
        client=lead,
        old_assignee=None,
        new_assignee=owner_user,
        actor=owner_user,
    )
    assert not Notification.objects.filter(
        user=owner_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_bulk_assign_skips_transfer_for_acting_previous_assignee(
    authenticated_admin, admin_user, company, employee_user, lead_statuses, subscription,
):
    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Bulk From Admin",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=admin_user,
    )
    Notification.objects.filter(user__in=[admin_user, employee_user]).delete()

    response = authenticated_admin.post(
        "/api/v1/clients/bulk_assign/",
        {"client_ids": [lead.id], "user_id": employee_user.id},
        format="json",
    )
    assert response.status_code == 200, getattr(response, "data", response.content)

    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()
    assert not Notification.objects.filter(
        user=admin_user,
        type=NotificationType.LEAD_TRANSFERRED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_status_change_skips_when_actor_is_assignee(
    company, employee_user, lead_statuses,
):
    status_new, status_contacted = lead_statuses
    lead = Client.objects.create(
        name="Self Status Change",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=employee_user,
    )
    Notification.objects.filter(user=employee_user).delete()

    lead.status = status_contacted
    lead._notification_actor = employee_user
    lead.save()

    assert not Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_STATUS_CHANGED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_assign_without_actor_still_notifies_assignee(
    company, employee_user, lead_statuses,
):
    """System / bare save paths (actor=None) must still notify the new assignee."""
    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="System Assign",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=None,
    )
    Notification.objects.filter(user=employee_user).delete()

    notify_lead_assignment_change(
        client=lead,
        old_assignee=None,
        new_assignee=employee_user,
        actor=None,
    )
    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_create_lead_assigned_to_creator_skips_lead_assigned(
    company, owner_user, lead_statuses,
):
    status_new, _ = lead_statuses
    Notification.objects.filter(user=owner_user).delete()
    Client.objects.create(
        name="Self Created Assigned",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=owner_user,
        created_by=owner_user,
    )
    assert not Notification.objects.filter(
        user=owner_user,
        type=NotificationType.LEAD_ASSIGNED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_deal_created_skips_actor_employee(
    company, owner_user, employee_user, lead_statuses,
):
    from crm.models import Deal

    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Deal Lead",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=employee_user,
    )
    Notification.objects.filter(user__in=[owner_user, employee_user]).delete()

    Deal.objects.create(
        client=lead,
        company=company,
        employee=employee_user,
        started_by=employee_user,
        stage="in_progress",
        value=1000,
    )

    assert not Notification.objects.filter(
        user=employee_user,
        type=NotificationType.DEAL_CREATED,
        deleted_at__isnull=True,
    ).exists()
    assert Notification.objects.filter(
        user=owner_user,
        type=NotificationType.DEAL_CREATED,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_deal_closed_skips_acting_employee(
    company, owner_user, employee_user, lead_statuses,
):
    from crm.models import Deal

    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Won Deal Lead",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=employee_user,
    )
    deal = Deal.objects.create(
        client=lead,
        company=company,
        employee=employee_user,
        started_by=employee_user,
        stage="in_progress",
        value=500,
    )
    Notification.objects.filter(user__in=[owner_user, employee_user]).delete()

    deal.stage = "won"
    deal.closed_by = employee_user
    deal._notification_actor = employee_user
    deal.save()

    assert not Notification.objects.filter(
        user=employee_user,
        type=NotificationType.DEAL_CLOSED,
        deleted_at__isnull=True,
    ).exists()
    assert Notification.objects.filter(
        user=owner_user,
        type=NotificationType.TEAM_ACTIVITY,
        deleted_at__isnull=True,
    ).exists()


@pytest.mark.django_db
def test_soft_delete_single_notification(api_client, employee_user, subscription):
    n = Notification.objects.create(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
        title="Lead Assigned",
        body="x",
        data={"lead_id": 1},
    )
    api_client.force_authenticate(user=employee_user)
    response = api_client.delete(f"/api/v1/notifications/{n.id}/delete/")
    assert response.status_code == 200, getattr(response, "data", response.content)
    n.refresh_from_db()
    assert n.deleted_at is not None
    listed = api_client.get("/api/v1/notifications/?page=1&page_size=100")
    body = listed.data
    if isinstance(body, dict) and body.get("success") is True and "data" in body:
        body = body["data"]
    ids = {row["id"] for row in body.get("results", [])}
    assert n.id not in ids


@pytest.mark.django_db
def test_list_api_returns_lead_notifications_not_hidden_by_tenant_chat_exclude(
    api_client, employee_user, subscription, company, lead_statuses,
):
    """
    Regression (Postgres + SQLite): bare exclude(data__kind='tenant_chat') drops
    rows without that key because JSON misses become SQL NULL. Inbox looked empty.
    """
    from notifications.views import exclude_tenant_chat_push_notifications

    status_new, _ = lead_statuses
    lead = Client.objects.create(
        name="Visible Inbox Lead",
        company=company,
        priority="medium",
        type="fresh",
        status=status_new,
        assigned_to=None,
    )
    lead.assigned_to = employee_user
    lead.assigned_at = timezone.now()
    lead.save()

    Notification.objects.create(
        user=employee_user,
        type=NotificationType.GENERAL,
        title="Chat",
        body="hi",
        data={"kind": "tenant_chat", "conversation_id": 1},
    )

    inbox = Notification.objects.filter(user=employee_user, deleted_at__isnull=True)
    visible = exclude_tenant_chat_push_notifications(inbox)
    assert visible.filter(type=NotificationType.LEAD_ASSIGNED).exists()
    assert not visible.filter(data__kind="tenant_chat").exists()

    api_client.force_authenticate(user=employee_user)
    response = api_client.get("/api/v1/notifications/?page=1&page_size=100")
    assert response.status_code == 200
    body = response.data
    if isinstance(body, dict) and body.get("success") is True and "data" in body:
        body = body["data"]
    results = body.get("results") if isinstance(body, dict) else body
    types = {row["type"] for row in results}
    assert NotificationType.LEAD_ASSIGNED in types
    assert not any((row.get("data") or {}).get("kind") == "tenant_chat" for row in results)
