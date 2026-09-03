"""
Device registration and the inbound-ringing-call push.

Two separate additions, tested together because they are the two halves of giving
the product push reach it did not have: knowing *what kind* of device a token
belongs to, and having something to send to a ringing call.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models import UserDevice


@pytest.mark.django_db
class TestUserDeviceRegistration:
    URL = "/api/v1/users/update-fcm-token/"

    def test_registering_records_the_platform(self, authenticated_admin, admin_user):
        response = authenticated_admin.post(
            self.URL,
            {"fcm_token": "tok-web-1", "platform": "web", "language": "en"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        device = UserDevice.objects.get(token="tok-web-1")
        assert device.user_id == admin_user.id
        assert device.platform == UserDevice.Platform.WEB

    def test_missing_platform_is_recorded_as_unknown(self, authenticated_admin):
        """Older mobile builds send no platform and must keep working."""
        response = authenticated_admin.post(
            self.URL, {"fcm_token": "tok-legacy"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK
        assert (
            UserDevice.objects.get(token="tok-legacy").platform
            == UserDevice.Platform.UNKNOWN
        )

    def test_garbage_platform_is_not_trusted(self, authenticated_admin):
        authenticated_admin.post(
            self.URL, {"fcm_token": "tok-x", "platform": "<script>"}, format="json"
        )
        assert (
            UserDevice.objects.get(token="tok-x").platform
            == UserDevice.Platform.UNKNOWN
        )

    def test_a_token_moves_to_whoever_registered_it_last(
        self, authenticated_admin, admin_user, employee_user, subscription
    ):
        """
        Two people signing in on one device.

        FCM issues a token per app install, so the second sign-in must take the
        token over. If it did not, the first user would keep receiving pushes on a
        device now being used by someone else.
        """
        from rest_framework.test import APIClient

        authenticated_admin.post(
            self.URL, {"fcm_token": "shared-device", "platform": "android"},
            format="json",
        )
        assert UserDevice.objects.get(token="shared-device").user_id == admin_user.id

        other = APIClient()
        other.force_authenticate(user=employee_user)
        other.post(
            self.URL, {"fcm_token": "shared-device", "platform": "android"},
            format="json",
        )

        assert UserDevice.objects.filter(token="shared-device").count() == 1
        assert UserDevice.objects.get(token="shared-device").user_id == employee_user.id


@pytest.mark.django_db
class TestPlatformTargetedTokens:
    def test_unfiltered_push_is_unchanged(self, admin_user):
        """
        The compatibility guarantee.

        Every existing sender calls this with no arguments, so it must keep
        returning the legacy token list exactly as before — device rows or not.
        """
        admin_user.add_fcm_token("legacy-1")
        admin_user.fcm_token = "legacy-2"
        admin_user.save(update_fields=["fcm_tokens", "fcm_token"])

        tokens = admin_user.iter_fcm_tokens_for_push()
        assert set(tokens) == {"legacy-1", "legacy-2"}

    def test_platform_filter_excludes_unknown_devices(self, admin_user):
        """
        A browser-only push must not buzz a phone.

        Legacy tokens have a genuinely unknown platform, so they are excluded from
        a targeted send — treating them as a match would defeat the filter, which
        is the entire reason the table exists.
        """
        UserDevice.objects.create(
            user=admin_user, token="web-1", platform=UserDevice.Platform.WEB
        )
        UserDevice.objects.create(
            user=admin_user, token="phone-1", platform=UserDevice.Platform.ANDROID
        )
        UserDevice.objects.create(
            user=admin_user, token="old-1", platform=UserDevice.Platform.UNKNOWN
        )

        assert admin_user.iter_fcm_tokens_for_push(platform="web") == ["web-1"]
        assert admin_user.iter_fcm_tokens_for_push(platform="android") == ["phone-1"]


@pytest.mark.django_db
class TestRingingCallPush:
    def _make_call(self, company, assigned_to=None):
        from crm.models import Client
        from integrations.models import (
            WhatsAppAccount,
            WhatsAppCall,
            WhatsAppCallDirection,
        )

        account = WhatsAppAccount.objects.create(
            company=company, waba_id="w", phone_number_id="pn-ring"
        )
        client = Client.objects.create(
            name="Caller",
            company=company,
            priority="low",
            type="cold",
            assigned_to=assigned_to,
        )
        return WhatsAppCall.objects.create(
            company=company,
            whatsapp_account=account,
            meta_call_id="ring-1",
            direction=WhatsAppCallDirection.INBOUND,
            peer_phone="964770000000",
            client=client,
        )

    def test_assigned_call_rings_only_the_assignee(
        self, company, admin_user, employee_user
    ):
        from integrations.services.whatsapp_call_push import (
            notify_inbound_ringing_call,
        )

        call = self._make_call(company, assigned_to=employee_user)

        with patch(
            "notifications.services.NotificationService.send_notification"
        ) as send:
            notify_inbound_ringing_call(call)

        notified = {c.kwargs["user"].id for c in send.call_args_list}
        assert notified == {employee_user.id}

    def test_away_agents_are_not_rung(self, company, employee_user):
        """Mirrors the UI: an Away agent is not offered the call, so must not be
        woken for it either."""
        from django.utils import timezone
        from datetime import timedelta

        from integrations.services.whatsapp_call_push import (
            notify_inbound_ringing_call,
        )

        employee_user.whatsapp_call_away_until = timezone.now() + timedelta(hours=1)
        employee_user.save(update_fields=["whatsapp_call_away_until"])

        call = self._make_call(company, assigned_to=employee_user)

        with patch(
            "notifications.services.NotificationService.send_notification"
        ) as send:
            notify_inbound_ringing_call(call)

        send.assert_not_called()

    def test_push_creates_no_inbox_row(self, company, employee_user):
        """
        A ring is transient — answered, missed or rejected within seconds. A bell
        entry for it would be stale before anyone read it; History is the record.
        """
        from integrations.services.whatsapp_call_push import (
            notify_inbound_ringing_call,
        )
        from notifications.models import Notification

        call = self._make_call(company, assigned_to=employee_user)

        with patch("notifications.services.NotificationService.deliver_push"):
            notify_inbound_ringing_call(call)

        assert not Notification.objects.filter(
            user=employee_user, type="whatsapp_call_incoming"
        ).exists()

    def test_failure_is_swallowed(self, company, employee_user):
        """
        This runs inside the Meta webhook. An exception would fail the webhook,
        Meta would retry it, and the retry would ring everyone a second time.
        """
        from integrations.services.whatsapp_call_push import (
            notify_inbound_ringing_call,
        )

        call = self._make_call(company, assigned_to=employee_user)

        with patch(
            "notifications.services.NotificationService.send_notification",
            side_effect=RuntimeError("firebase down"),
        ):
            assert notify_inbound_ringing_call(call) == 0

    def test_incoming_call_rings_rather_than_chimes(self):
        from notifications.fcm_android_channels import (
            android_notification_channel_id,
            is_arrival_ring_notification_type,
        )

        assert is_arrival_ring_notification_type("whatsapp_call_incoming") is True
        assert android_notification_channel_id("whatsapp_call_incoming") == "arrival"
