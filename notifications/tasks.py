"""
Queued push delivery.

Push is the only part of sending a notification that leaves the process. Every
other step — mute checks, translation, writing the inbox row — is local DB work
that finishes in milliseconds, so only delivery is worth deferring.

Why this matters on this deployment: Gunicorn has a small, fixed number of request
slots, and an FCM call that hangs occupies one for its full duration. Enough
simultaneous slow calls and the API stops answering anyone, for a reason that has
nothing to do with how many users are online.

Nothing here is on by default. ``PUSH_QUEUE_ENABLED`` gates it so the code can ship
before the cluster exists, and so it can be turned off again without a redeploy if
the cluster misbehaves.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

User = get_user_model()

# Dotted path rather than the function object: django-q stores the reference in the
# broker and re-imports it in the worker, so it must be resolvable by name there.
PUSH_TASK_PATH = "notifications.tasks.deliver_push_task"


def push_queue_enabled() -> bool:
    """
    True when pushes should be enqueued instead of sent inline.

    Read at call time, not import time, so tests and management commands can flip
    it with override_settings without reloading the module.
    """
    return bool(getattr(settings, "PUSH_QUEUE_ENABLED", False))


def deliver_push_task(
    user_id: int,
    notification_type: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    image_url: Optional[str] = None,
) -> bool:
    """
    Worker entry point. Runs in the qcluster, not in a request.

    Takes a user id rather than a User because the payload is pickled into the
    broker and may sit there briefly; re-reading the row means delivery uses the
    user's current tokens and not a snapshot from whenever it was queued.
    """
    # Imported here, not at module scope: services imports this module for
    # enqueue_push, so a top-level import back into services would be circular.
    from .services import NotificationService

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        # Deleted between enqueue and delivery. Nothing to do, and not an error.
        logger.info("Skipping queued push for missing user id=%s", user_id)
        return False

    return NotificationService.deliver_push(
        user,
        notification_type=notification_type,
        title=title,
        body=body,
        data=data,
        image_url=image_url,
    )


def enqueue_push(
    user_id: int,
    notification_type: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    image_url: Optional[str] = None,
) -> bool:
    """
    Hand a push to the cluster.

    Returns True when the broker accepted it. On any failure this returns False
    instead of raising, so the caller can deliver inline — a push that is slow is
    a worse outcome than a fast request, but a push that vanishes because Redis
    was briefly unreachable is worse than both.
    """
    try:
        from django_q.tasks import async_task

        async_task(
            PUSH_TASK_PATH,
            user_id,
            notification_type,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
            task_name=f"push:{notification_type}:{user_id}"[:100],
        )
        return True
    except Exception as exc:
        logger.warning(
            "Could not enqueue push for user id=%s (%s); delivering inline instead",
            user_id,
            exc,
        )
        return False
