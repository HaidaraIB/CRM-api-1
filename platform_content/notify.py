"""Notify company owners about a published news post (manual admin action)."""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

NOTIFY_CHANNELS = frozenset({"push", "email", "both"})


def _feature_title_for_user(news, user) -> str:
    lang = (getattr(user, "language", None) or "ar").lower()
    if lang.startswith("ar"):
        return (news.title_ar or news.title_en or "").strip()
    return (news.title_en or news.title_ar or "").strip()


def _owner_has_news_inbox(owner_id: int, news_id: int) -> bool:
    """True if this owner already has an inbox row for this news post."""
    from notifications.models import Notification, NotificationType

    return Notification.objects.filter(
        user_id=owner_id,
        type=NotificationType.SYSTEM_UPDATE,
        data__news_id=str(news_id),
    ).exists()


def notify_company_owners_news(
    news_id: int,
    channels: str = "both",
) -> None:
    """
    Notify every company owner about a published news post.

    channels:
      - push: in-app inbox + FCM only
      - email: email only
      - both: inbox + FCM + email

    Inbox rows are created at most once per owner per news post. Re-notifying
    still sends push/email when requested, but skips a duplicate inbox record.
    """
    from accounts.event_emails import send_news_published_email
    from accounts.models import User
    from accounts.utils import get_email_language_for_user
    from companies.models import Company
    from notifications.models import NotificationType
    from notifications.services import NotificationService

    from .models import NewsPost

    channel = (channels or "both").strip().lower()
    if channel not in NOTIFY_CHANNELS:
        logger.warning("Invalid news notify channels %r for news %s", channels, news_id)
        return

    send_push = channel in ("push", "both")
    send_email = channel in ("email", "both")

    try:
        news = NewsPost.objects.get(pk=news_id, is_published=True)
    except NewsPost.DoesNotExist:
        logger.warning("News notify skipped; post %s missing/unpublished", news_id)
        return

    owner_ids = list(
        Company.objects.exclude(owner_id=None)
        .values_list("owner_id", flat=True)
        .distinct()
    )
    if not owner_ids:
        return

    owners = User.objects.filter(id__in=owner_ids, is_active=True)
    for owner in owners.iterator(chunk_size=100):
        feature = _feature_title_for_user(news, owner) or "LOOP CRM"

        if send_push:
            try:
                skip_inbox = _owner_has_news_inbox(owner.pk, news.id)
                NotificationService.send_notification(
                    user=owner,
                    notification_type=NotificationType.SYSTEM_UPDATE,
                    data={
                        "feature": feature,
                        "news_id": str(news.id),
                        "action": "open_news",
                        "type": NotificationType.SYSTEM_UPDATE.value,
                    },
                    skip_settings_check=True,
                    skip_database_insert=skip_inbox,
                )
            except Exception:
                logger.exception(
                    "Failed news push/inbox for owner %s news %s", owner.pk, news_id
                )

        if send_email and owner.email:
            try:
                language = get_email_language_for_user(owner, request=None, default="en")
                send_news_published_email(owner, news, language=language)
            except Exception:
                logger.exception(
                    "Failed news email for owner %s news %s", owner.pk, news_id
                )


def notify_company_owners_news_async(news_id: int, channels: str = "both") -> None:
    """Fire-and-forget so admin CMS responses are not blocked by FCM/SMTP."""

    def _run():
        try:
            notify_company_owners_news(news_id, channels=channels)
        except Exception:
            logger.exception(
                "Async news notify failed for news %s channels=%s", news_id, channels
            )

    threading.Thread(target=_run, daemon=True).start()


# Back-compat aliases (unused after manual notify; keep imports from breaking)
def notify_company_owners_news_published(news_id: int) -> None:
    notify_company_owners_news(news_id, channels="both")


def notify_company_owners_news_published_async(news_id: int) -> None:
    notify_company_owners_news_async(news_id, channels="both")
