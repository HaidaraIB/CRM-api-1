"""Rolled-back transactions must not dispatch notifications."""

import pytest
from django.db import transaction

from notifications.models import Notification, NotificationType
from notifications.services import NotificationService


@pytest.mark.django_db(transaction=True)
@pytest.mark.real_on_commit
def test_send_notification_on_commit_skips_on_rollback(employee_user):
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            NotificationService.send_notification_on_commit(
                employee_user,
                notification_type=NotificationType.LEAD_ASSIGNED,
                data={"lead_id": 1, "lead_name": "X"},
            )
            raise RuntimeError("rollback")

    assert not Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
    ).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.real_on_commit
def test_send_notification_on_commit_runs_after_commit(employee_user):
    with transaction.atomic():
        NotificationService.send_notification_on_commit(
            employee_user,
            notification_type=NotificationType.LEAD_ASSIGNED,
            data={"lead_id": 1, "lead_name": "X"},
        )
    assert Notification.objects.filter(
        user=employee_user,
        type=NotificationType.LEAD_ASSIGNED,
    ).exists()
