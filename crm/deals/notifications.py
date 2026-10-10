"""Deal notifications. Called from DealService, not from model signals."""

import logging

from notifications.models import NotificationType
from notifications.services import NotificationService
from notifications.team_activity import notify_owner_team_activity

logger = logging.getLogger(__name__)


def deal_label(deal) -> str:
    name = (deal.title or "").strip()
    if not name and deal.client_id:
        name = deal.client.name
    return f"{name or 'Deal'} - {deal.value or 0}"


def _payload(deal):
    return {
        "deal_id": deal.id,
        "deal_title": deal_label(deal),
        "value": str(deal.value or 0),
        "invalidate": "crm:deals",
    }


def _skip_actor(recipients, actor):
    actor_id = actor.pk if actor is not None else None
    for user in recipients:
        if user is None:
            continue
        if actor_id is not None and user.pk == actor_id:
            continue
        yield user


def notify_deal_created(deal, actor):
    try:
        recipients = []
        if deal.employee_id:
            recipients.append(deal.employee)
        owner = getattr(deal.company, "owner", None) if deal.company_id else None
        if owner and (not deal.employee_id or owner.pk != deal.employee_id):
            recipients.append(owner)
        payload = _payload(deal)
        for recipient in _skip_actor(recipients, actor):
            NotificationService.send_notification_on_commit(
                user=recipient,
                notification_type=NotificationType.DEAL_CREATED,
                data=payload,
            )
    except Exception:
        logger.exception("Error sending deal created notification")


def notify_deal_won(deal, actor):
    try:
        payload = _payload(deal)
        if deal.employee_id and (actor is None or deal.employee_id != actor.pk):
            NotificationService.send_notification_on_commit(
                user=deal.employee,
                notification_type=NotificationType.DEAL_CLOSED,
                data=payload,
            )
        notify_owner_team_activity(
            actor or deal.employee,
            deal.company,
            action="deal_won",
            deal_id=deal.id,
            deal_title=deal_label(deal),
            lead_id=deal.client_id,
            lead_name=deal.client.name if deal.client_id else "",
            value=str(deal.value or 0),
        )
    except Exception:
        logger.exception("Error sending deal won notification")


def notify_deal_lost(deal, actor):
    try:
        payload = _payload(deal)
        recipients = []
        if deal.employee_id:
            recipients.append(deal.employee)
        owner = getattr(deal.company, "owner", None) if deal.company_id else None
        if owner and (not deal.employee_id or owner.pk != deal.employee_id):
            recipients.append(owner)
        for recipient in _skip_actor(recipients, actor):
            NotificationService.send_notification_on_commit(
                user=recipient,
                notification_type=NotificationType.DEAL_UPDATED,
                title="Deal lost",
                body=f"Deal {deal_label(deal)} was marked lost",
                data=payload,
            )
    except Exception:
        logger.exception("Error sending deal lost notification")


def notify_deal_reassigned(deal, actor):
    try:
        if not deal.employee_id:
            return
        if actor is not None and deal.employee_id == actor.pk:
            return
        NotificationService.send_notification_on_commit(
            user=deal.employee,
            notification_type=NotificationType.DEAL_UPDATED,
            data=_payload(deal),
        )
    except Exception:
        logger.exception("Error sending deal reassignment notification")
