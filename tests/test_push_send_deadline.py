"""
Wall-clock cap on a single FCM multicast.

A send that stalls past Q_CLUSTER["timeout"] gets the qcluster worker killed and the
push recorded as a failed task. The cap turns that into an ordinary, logged
non-delivery that returns well inside the worker's budget.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from firebase_admin import messaging

from notifications import services
from notifications.services import NotificationService

_USER = SimpleNamespace(username="pushee")


def test_stalled_send_is_abandoned_at_the_deadline():
    release = threading.Event()

    def _hang(_multicast):
        release.wait(5)

    try:
        with (
            patch.object(services, "_FCM_SEND_DEADLINE_SECONDS", 0.1),
            patch.object(messaging, "send_each_for_multicast", side_effect=_hang),
        ):
            started = time.monotonic()
            result = NotificationService._send_multicast(_USER, ["tok-a"], object())
            elapsed = time.monotonic() - started
    finally:
        release.set()

    assert result == (0, [])
    assert elapsed < 2


def test_stalled_send_logs_where_the_sdk_pool_thread_is_stuck():
    """The SDK does the HTTP call on its own pool thread; that's the stack we need."""
    release = threading.Event()

    def _oauth_refresh_hangs():
        release.wait(5)

    def _sdk_send(_multicast):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_oauth_refresh_hangs).result()

    try:
        with (
            patch.object(services, "_FCM_SEND_DEADLINE_SECONDS", 0.2),
            patch.object(messaging, "send_each_for_multicast", side_effect=_sdk_send),
            patch.object(services.logger, "error") as log_error,
        ):
            NotificationService._send_multicast(_USER, ["tok-a"], object())
    finally:
        release.set()

    template, *args = log_error.call_args.args
    message = template % tuple(args)
    assert "FCM send to pushee exceeded" in message
    assert "--- thread fcm-send ---" in message
    assert "_oauth_refresh_hangs" in message


def test_completed_send_reports_success_and_stale_tokens():
    batch = SimpleNamespace(
        success_count=1,
        responses=[
            SimpleNamespace(success=True, exception=None),
            SimpleNamespace(success=False, exception=messaging.UnregisteredError("gone")),
        ],
    )
    with patch.object(messaging, "send_each_for_multicast", return_value=batch):
        result = NotificationService._send_multicast(_USER, ["tok-a", "tok-b"], object())

    assert result == (1, ["tok-b"])


def test_send_error_still_reaches_the_caller():
    """deliver_push's own except block logs these; the thread must not swallow them."""
    with patch.object(
        messaging, "send_each_for_multicast", side_effect=ValueError("bad message")
    ):
        with pytest.raises(ValueError, match="bad message"):
            NotificationService._send_multicast(_USER, ["tok-a"], object())


def test_two_sends_fit_inside_the_worker_timeout(settings):
    """deliver_push sends web then native; both capped sends must beat the kill."""
    assert 2 * services._FCM_SEND_DEADLINE_SECONDS < settings.Q_CLUSTER["timeout"]
