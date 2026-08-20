"""
Queued push delivery (PUSH_QUEUE_ENABLED).

The property that matters most here is the fallback: enqueueing must never be able
to lose a push. If the broker is unreachable the send has to happen inline, slowly,
rather than disappear — a slow request is recoverable, a silent loss is not.
"""

from unittest.mock import patch

import pytest

from notifications.models import Notification, NotificationType
from notifications.services import NotificationService


@pytest.fixture
def _user_with_token(employee_user):
    """Push is skipped entirely for a user with no FCM token."""
    employee_user.fcm_tokens = ["tok-abc123"]
    employee_user.save(update_fields=["fcm_tokens"])
    return employee_user


@pytest.mark.django_db
class TestPushQueue:
    def test_disabled_by_default_delivers_inline(self, settings, _user_with_token):
        """Default config must behave exactly as it did before the queue existed."""
        assert settings.PUSH_QUEUE_ENABLED is False

        with (
            patch.object(NotificationService, "deliver_push", return_value=True) as inline,
            patch("notifications.services.enqueue_push") as enqueued,
        ):
            NotificationService.send_notification(
                _user_with_token,
                notification_type=NotificationType.LEAD_ASSIGNED,
                data={"lead_id": 1, "lead_name": "X"},
            )

        inline.assert_called_once()
        enqueued.assert_not_called()

    def test_enabled_defers_delivery(self, settings, _user_with_token):
        settings.PUSH_QUEUE_ENABLED = True

        with (
            patch.object(NotificationService, "deliver_push") as inline,
            patch("notifications.services.enqueue_push", return_value=True) as enqueued,
        ):
            NotificationService.send_notification(
                _user_with_token,
                notification_type=NotificationType.LEAD_ASSIGNED,
                data={"lead_id": 1, "lead_name": "X"},
            )

        enqueued.assert_called_once()
        assert enqueued.call_args.kwargs["user_id"] == _user_with_token.pk
        inline.assert_not_called()

    def test_broker_failure_falls_back_to_inline(self, settings, _user_with_token):
        """The anti-loss guarantee: a refused enqueue still sends."""
        settings.PUSH_QUEUE_ENABLED = True

        with (
            patch.object(NotificationService, "deliver_push", return_value=True) as inline,
            patch("notifications.services.enqueue_push", return_value=False),
        ):
            NotificationService.send_notification(
                _user_with_token,
                notification_type=NotificationType.LEAD_ASSIGNED,
                data={"lead_id": 1, "lead_name": "X"},
            )

        inline.assert_called_once()

    def test_enqueue_swallows_broker_errors(self):
        """enqueue_push reports failure rather than raising into the request."""
        from notifications.tasks import enqueue_push

        with patch("django_q.tasks.async_task", side_effect=OSError("redis down")):
            assert (
                enqueue_push(user_id=1, notification_type=NotificationType.LEAD_ASSIGNED)
                is False
            )

    def test_inbox_row_written_even_when_queued(self, settings, _user_with_token):
        """
        Queuing defers only delivery. The inbox row — which the sidebar badge and
        /sync/digest/ both read — must still be committed synchronously.
        """
        settings.PUSH_QUEUE_ENABLED = True

        with patch("notifications.services.enqueue_push", return_value=True):
            NotificationService.send_notification(
                _user_with_token,
                notification_type=NotificationType.LEAD_ASSIGNED,
                data={"lead_id": 1, "lead_name": "X"},
            )

        assert Notification.objects.filter(
            user=_user_with_token,
            type=NotificationType.LEAD_ASSIGNED,
        ).exists()

    def test_real_enqueue_matches_task_signature(self, _user_with_token):
        """
        Exercises the actual django-q call path rather than a mock of it.

        This is the contract the other tests cannot see: `task_name` must be
        consumed by async_task as a reserved option, and every remaining kwarg must
        arrive on deliver_push_task. A mismatch would not fail at enqueue time — it
        would fail inside the worker, in production, as pushes that quietly never
        arrive. Conf.SYNC makes the cluster run the task in-process so the whole
        round trip is checked here.
        """
        from django_q.conf import Conf

        from notifications.tasks import enqueue_push

        with (
            patch.object(Conf, "SYNC", True),
            patch.object(NotificationService, "deliver_push", return_value=True) as delivered,
        ):
            assert (
                enqueue_push(
                    user_id=_user_with_token.pk,
                    notification_type=NotificationType.LEAD_ASSIGNED,
                    title="T",
                    body="B",
                    data={"lead_id": 1},
                    image_url=None,
                )
                is True
            )

        delivered.assert_called_once()
        assert delivered.call_args.args[0].pk == _user_with_token.pk
        assert delivered.call_args.kwargs["title"] == "T"
        assert delivered.call_args.kwargs["data"] == {"lead_id": 1}

    def test_worker_task_tolerates_deleted_user(self):
        """A user removed between enqueue and delivery is a no-op, not a crash."""
        from notifications.tasks import deliver_push_task

        assert (
            deliver_push_task(
                user_id=999_999_999,
                notification_type=NotificationType.LEAD_ASSIGNED,
            )
            is False
        )
