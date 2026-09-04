"""
Broadcasts are sent exactly once, even with two senders racing.

Before this, ``send_scheduled_broadcasts`` selected every PENDING broadcast, sent
it, and only then wrote ``status=SENT``. Nothing held the row in between, so any
two senders overlapping in that window both passed the status check and both sent:
cron has no overlap lock, so a send lasting longer than a minute raced the next
run, and the admin panel's "send now" button raced both. Every recipient got the
message once per racer.

The fix is a compare-and-swap into a SENDING state plus a per-recipient delivery
ledger, so a killed sender resumes instead of restarting. These tests pin both
halves, and the interaction between them: reclaiming a stale send is only safe
*because* the ledger exists.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from subscriptions.models import (
    SENDING_CLAIM_STALE_AFTER,
    Broadcast,
    BroadcastStatus,
    BroadcastType,
)


@pytest.fixture
def recipients(company, db):
    """Four users with emails, standing in for whatever the targets resolve to."""
    from accounts.models import User

    return [
        User.objects.create_user(
            username=f"recipient_{i}",
            email=f"recipient{i}@test.com",
            password="testpass123",
            first_name=f"Recipient{i}",
            last_name="User",
            company=company,
            role="admin",
        )
        for i in range(4)
    ]


@pytest.fixture
def broadcast(db):
    return Broadcast.objects.create(
        subject="Scheduled maintenance",
        content="We will be down at midnight.",
        targets=["role_admin"],
        broadcast_type=BroadcastType.EMAIL.value,
        status=BroadcastStatus.PENDING.value,
        scheduled_at=timezone.now() - timedelta(seconds=10),
    )


@pytest.fixture
def sending(recipients):
    """
    Neutralise the transport, keep the loop.

    Patches the recipient resolver so these tests are about claiming and resuming
    rather than target resolution, and the SMTP connection so nothing leaves the
    process. Yields the mock standing in for the outbound send, so a test can
    count deliveries or make one fail.
    """
    smtp = MagicMock()
    smtp.is_active = True

    with patch(
        "subscriptions.utils.get_recipient_users_for_email_broadcast",
        return_value=list(recipients),
    ), patch(
        "subscriptions.utils.SMTPSettings.get_settings", return_value=smtp
    ), patch(
        "subscriptions.utils.get_smtp_connection", return_value=MagicMock()
    ), patch(
        "django.core.mail.EmailMultiAlternatives.send"
    ) as send:
        yield send


@pytest.mark.django_db
class TestClaim:
    def test_only_one_claimant_wins(self, broadcast):
        """The whole point: of two senders on one row, exactly one sends."""
        first = Broadcast.objects.get(pk=broadcast.pk)
        second = Broadcast.objects.get(pk=broadcast.pk)

        assert first.claim_for_sending() is True
        assert second.claim_for_sending() is False

        broadcast.refresh_from_db()
        assert broadcast.status == BroadcastStatus.SENDING.value
        assert broadcast.sending_started_at is not None

    def test_a_sent_broadcast_is_never_claimable(self, broadcast):
        broadcast.status = BroadcastStatus.SENT.value
        broadcast.save(update_fields=["status"])

        assert broadcast.claim_for_sending() is False

    def test_a_draft_is_claimable(self, broadcast):
        """`status` is null for drafts, which "send now" is allowed to send."""
        broadcast.status = None
        broadcast.save(update_fields=["status"])

        assert broadcast.claim_for_sending() is True

    def test_a_failed_broadcast_can_be_retried(self, broadcast):
        broadcast.status = BroadcastStatus.FAILED.value
        broadcast.save(update_fields=["status"])

        assert broadcast.claim_for_sending() is True

    def test_a_live_claim_is_not_stolen(self, broadcast):
        """A slow but healthy send must not be interrupted by the next runner."""
        broadcast.status = BroadcastStatus.SENDING.value
        broadcast.sending_started_at = timezone.now() - timedelta(minutes=5)
        broadcast.save(update_fields=["status", "sending_started_at"])

        assert Broadcast.objects.get(pk=broadcast.pk).claim_for_sending() is False

    def test_a_stale_claim_is_reclaimable(self, broadcast):
        """Otherwise a killed worker strands the broadcast forever."""
        broadcast.status = BroadcastStatus.SENDING.value
        broadcast.sending_started_at = (
            timezone.now() - SENDING_CLAIM_STALE_AFTER - timedelta(minutes=1)
        )
        broadcast.save(update_fields=["status", "sending_started_at"])

        assert Broadcast.objects.get(pk=broadcast.pk).claim_for_sending() is True


@pytest.mark.django_db
class TestDeliveryLedger:
    def test_every_recipient_is_recorded(self, broadcast, recipients, sending):
        from subscriptions.utils import send_broadcast_email

        result = send_broadcast_email(broadcast)

        assert result["success"] is True
        assert result["recipients_count"] == 4
        broadcast.refresh_from_db()
        assert set(broadcast.delivered_user_ids) == {u.id for u in recipients}

    def test_progress_survives_a_crash_partway(self, broadcast, recipients, sending):
        """
        The ledger is written per recipient, not at the end.

        If it were batched, the batch lost when a worker is killed is exactly the
        set of people who get a second copy on the retry.
        """
        from subscriptions.utils import send_broadcast_email

        sending.side_effect = [None, None, RuntimeError("worker killed"), None]

        result = send_broadcast_email(broadcast)

        assert result["success"] is False
        broadcast.refresh_from_db()
        assert len(broadcast.delivered_user_ids) == 2

    def test_a_resumed_send_skips_delivered_recipients(
        self, broadcast, recipients, sending
    ):
        from subscriptions.utils import send_broadcast_email

        broadcast.delivered_user_ids = [recipients[0].id, recipients[1].id]
        broadcast.save(update_fields=["delivered_user_ids"])

        result = send_broadcast_email(broadcast)

        assert result["success"] is True
        assert result["recipients_count"] == 2
        assert result["already_delivered"] == 2
        assert sending.call_count == 2
        broadcast.refresh_from_db()
        assert set(broadcast.delivered_user_ids) == {u.id for u in recipients}

    def test_resending_a_fully_delivered_broadcast_sends_nothing(
        self, broadcast, recipients, sending
    ):
        from subscriptions.utils import send_broadcast_email

        broadcast.delivered_user_ids = [u.id for u in recipients]
        broadcast.save(update_fields=["delivered_user_ids"])

        result = send_broadcast_email(broadcast)

        assert result["success"] is True
        assert result["recipients_count"] == 0
        assert sending.call_count == 0


@pytest.mark.django_db
class TestScheduledCommand:
    def test_overlapping_runs_send_once(self, broadcast, recipients, sending):
        """
        The regression this change exists for.

        The second run represents cron firing again while the first is still
        sending — it must find the row claimed and leave it alone.
        """
        call_command("send_scheduled_broadcasts")
        call_command("send_scheduled_broadcasts")

        assert sending.call_count == 4
        broadcast.refresh_from_db()
        assert broadcast.status == BroadcastStatus.SENT.value

    def test_a_sending_broadcast_is_left_alone(self, broadcast, recipients, sending):
        broadcast.status = BroadcastStatus.SENDING.value
        broadcast.sending_started_at = timezone.now()
        broadcast.save(update_fields=["status", "sending_started_at"])

        call_command("send_scheduled_broadcasts")

        assert sending.call_count == 0

    def test_a_stalled_send_is_resumed_not_restarted(
        self, broadcast, recipients, sending
    ):
        """
        Recovery, and the reason the ledger and the claim belong together.

        The stale arm of the query ignores the check-minutes window on purpose:
        by the time a killed send is noticed, the window has long since moved past
        the broadcast's scheduled time.
        """
        broadcast.status = BroadcastStatus.SENDING.value
        broadcast.sending_started_at = (
            timezone.now() - SENDING_CLAIM_STALE_AFTER - timedelta(minutes=1)
        )
        broadcast.scheduled_at = timezone.now() - timedelta(hours=6)
        broadcast.delivered_user_ids = [recipients[0].id, recipients[1].id]
        broadcast.save(
            update_fields=[
                "status",
                "sending_started_at",
                "scheduled_at",
                "delivered_user_ids",
            ]
        )

        call_command("send_scheduled_broadcasts")

        assert sending.call_count == 2
        broadcast.refresh_from_db()
        assert broadcast.status == BroadcastStatus.SENT.value
        assert set(broadcast.delivered_user_ids) == {u.id for u in recipients}

    def test_the_claim_is_released_on_success(self, broadcast, recipients, sending):
        call_command("send_scheduled_broadcasts")

        broadcast.refresh_from_db()
        assert broadcast.status == BroadcastStatus.SENT.value
        assert broadcast.sending_started_at is None
        assert broadcast.sent_at is not None

    def test_the_claim_is_released_on_failure(self, broadcast, recipients, sending):
        """A failed send must not look like one that is still running."""
        sending.side_effect = RuntimeError("smtp down")

        call_command("send_scheduled_broadcasts")

        broadcast.refresh_from_db()
        assert broadcast.status == BroadcastStatus.FAILED.value
        assert broadcast.sending_started_at is None


@pytest.fixture
def super_admin_client(api_client, db):
    from accounts.models import User

    user = User.objects.create_user(
        username="platform_admin",
        email="platform@test.com",
        password="testpass123",
        first_name="Platform",
        last_name="Admin",
        role="admin",
        is_superuser=True,
        is_staff=True,
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
class TestSendNowEndpoint:
    """The admin panel's "send now" button, the other half of the race."""

    def url(self, broadcast):
        return f"/api/v1/broadcasts/{broadcast.id}/send/"

    def test_it_refuses_a_broadcast_already_being_sent(
        self, super_admin_client, broadcast, recipients, sending
    ):
        Broadcast.objects.get(pk=broadcast.pk).claim_for_sending()

        response = super_admin_client.post(self.url(broadcast), {}, format="json")

        assert response.status_code == 400
        assert sending.call_count == 0

    def test_a_successful_send_releases_the_claim(
        self, super_admin_client, broadcast, recipients, sending
    ):
        response = super_admin_client.post(self.url(broadcast), {}, format="json")

        assert response.status_code == 200
        broadcast.refresh_from_db()
        assert broadcast.status == BroadcastStatus.SENT.value
        assert broadcast.sending_started_at is None

    def test_a_failed_send_leaves_a_draft_a_draft(
        self, super_admin_client, broadcast, recipients, sending
    ):
        """
        Claiming must not be visible when it fails.

        The admin panel gates a draft's own actions on `status === 'draft'`, so
        forcing FAILED here would strand the draft with no way to edit or resend.
        """
        broadcast.status = None
        broadcast.scheduled_at = None
        broadcast.save(update_fields=["status", "scheduled_at"])
        sending.side_effect = RuntimeError("smtp down")

        response = super_admin_client.post(self.url(broadcast), {}, format="json")

        assert response.status_code == 400
        broadcast.refresh_from_db()
        assert broadcast.status is None
        assert broadcast.sending_started_at is None
