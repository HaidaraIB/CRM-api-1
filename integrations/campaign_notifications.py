"""
Notifications for the Messaging Center campaign-request approval workflow.

Simpler sibling of notifications/team_activity.py: these are direct 1:1 notices
between a requester and the company owner (not a fan-out to opted-in
supervisors), and they are actionable/blocking rather than a digest-style
team-activity feed, so there is no per-user opt-in gate here.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.contrib.auth import get_user_model

from notifications.models import NotificationType
from notifications.services import NotificationService

logger = logging.getLogger(__name__)
User = get_user_model()


def _lang(user) -> str:
    return "ar" if getattr(user, "language", "ar") == "ar" else "en"


def _channel_label(channel: str, lang: str) -> str:
    if channel == "sms":
        return "SMS" if lang == "en" else "رسائل نصية"
    return "WhatsApp"


def notify_owner_of_campaign_request(batch) -> bool:
    """Notify the company owner that a new (or resubmitted) campaign request is pending."""
    company = batch.company
    owner_id = getattr(company, "owner_id", None)
    if not owner_id:
        return False
    owner = User.objects.filter(pk=owner_id, is_active=True).first()
    if owner is None:
        return False

    lang = _lang(owner)
    requester_name = ""
    if batch.requested_by is not None:
        requester_name = (batch.requested_by.get_full_name() or batch.requested_by.username or "").strip()
    channel_label = _channel_label(batch.channel, lang)

    if lang == "ar":
        title = "طلب حملة رسائل بانتظار الموافقة"
        body = f"{requester_name} يطلب إرسال حملة {channel_label} إلى {batch.recipient_count} من العملاء المحتملين."
    else:
        title = "Message campaign request pending approval"
        body = f"{requester_name} requested a {channel_label} campaign to {batch.recipient_count} lead(s)."

    return NotificationService.send_notification_on_commit(
        owner,
        notification_type=NotificationType.MESSAGE_CAMPAIGN_REQUEST_SUBMITTED,
        title=title,
        body=body,
        data={
            "batch_id": batch.id,
            "requested_by": batch.requested_by_id,
            "recipient_count": batch.recipient_count,
            "channel": batch.channel,
        },
        language=lang,
    )


def notify_requester_of_review(batch) -> bool:
    """Notify the requester their campaign request was approved or rejected."""
    requester = batch.requested_by
    if requester is None:
        return False

    lang = _lang(requester)
    channel_label = _channel_label(batch.channel, lang)

    from integrations.models import CampaignBatchStatus

    if batch.status == CampaignBatchStatus.REJECTED:
        notif_type = NotificationType.MESSAGE_CAMPAIGN_REQUEST_REJECTED
        if lang == "ar":
            title = "تم رفض طلب الحملة"
            body = (
                f"تم رفض طلب حملة {channel_label} الخاص بك"
                + (f": {batch.rejection_reason}" if batch.rejection_reason else ".")
            )
        else:
            title = "Campaign request rejected"
            body = f"Your {channel_label} campaign request was rejected" + (
                f": {batch.rejection_reason}" if batch.rejection_reason else "."
            )
    else:
        notif_type = NotificationType.MESSAGE_CAMPAIGN_REQUEST_APPROVED
        if lang == "ar":
            title = "تمت الموافقة على طلب الحملة"
            body = f"تمت الموافقة على طلب حملة {channel_label} الخاص بك، وسيتم إرسالها الآن."
        else:
            title = "Campaign request approved"
            body = f"Your {channel_label} campaign request was approved and will be sent shortly."

    return NotificationService.send_notification_on_commit(
        requester,
        notification_type=notif_type,
        title=title,
        body=body,
        data={
            "batch_id": batch.id,
            "rejection_reason": batch.rejection_reason,
        },
        language=lang,
    )


def notify_campaign_batch_complete(batch) -> None:
    """Notify both the requester and the owner once a queued send finishes."""
    company = batch.company
    recipients = []
    if batch.requested_by is not None:
        recipients.append(batch.requested_by)
    owner_id = getattr(company, "owner_id", None)
    if owner_id and owner_id != getattr(batch.requested_by, "id", None):
        owner = User.objects.filter(pk=owner_id, is_active=True).first()
        if owner is not None:
            recipients.append(owner)

    for user in recipients:
        lang = _lang(user)
        channel_label = _channel_label(batch.channel, lang)
        if lang == "ar":
            title = "اكتملت حملة الرسائل"
            body = f"حملة {channel_label}: تم الإرسال إلى {batch.sent_count} وفشل {batch.failed_count} من أصل {batch.recipient_count}."
        else:
            title = "Message campaign completed"
            body = (
                f"{channel_label} campaign: sent to {batch.sent_count}, "
                f"failed {batch.failed_count} of {batch.recipient_count}."
            )
        try:
            NotificationService.send_notification(
                user,
                NotificationType.MESSAGE_CAMPAIGN_REQUEST_COMPLETED,
                title=title,
                body=body,
                data={
                    "batch_id": batch.id,
                    "sent_count": batch.sent_count,
                    "failed_count": batch.failed_count,
                    "recipient_count": batch.recipient_count,
                },
                language=lang,
            )
        except Exception:
            logger.exception("Failed to notify user=%s of campaign batch completion id=%s", user.pk, batch.id)
