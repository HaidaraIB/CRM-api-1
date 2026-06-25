"""FCM push when an inbound WhatsApp message is stored from the Meta webhook."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from notifications.models import NotificationType
from notifications.services import NotificationService

if TYPE_CHECKING:
    from crm.models import Client

logger = logging.getLogger(__name__)


def _whatsapp_inbound_recipients(client: Client) -> list:
    """Assigned employee first; company owner if unassigned."""
    if client.assigned_to_id and getattr(client.assigned_to, "is_active", True):
        return [client.assigned_to]
    company = client.company
    if company is None:
        return []
    owner = getattr(company, "owner", None)
    if owner and getattr(owner, "is_active", True):
        return [owner]
    return []


def notify_whatsapp_inbound(
    *,
    client: Client,
    body: str,
    phone: str,
    message_id: str | None = None,
) -> None:
    """
    Push to assigned employee (or company owner fallback) when a lead replies on WhatsApp.
    Failures are logged only; webhook processing must not depend on push delivery.
    """
    try:
        from crm.models import Client

        client = Client.objects.select_related("assigned_to", "company", "company__owner").get(
            pk=client.pk
        )
        recipients = _whatsapp_inbound_recipients(client)
        if not recipients:
            logger.info(
                "WhatsApp inbound push skipped: no recipient client_id=%s company_id=%s",
                client.id,
                client.company_id,
            )
            return

        preview = (body or "").strip().replace("\n", " ")
        if len(preview) > 160:
            preview = preview[:157] + "..."

        title = (client.name or phone or "").strip() or phone
        push_body = preview or title

        data = {
            "lead_id": str(client.id),
            "lead_name": client.name or "",
            "phone": phone,
            "message_id": message_id or "",
            "message_preview": preview,
        }

        for user in recipients:
            NotificationService.send_notification(
                user=user,
                notification_type=NotificationType.WHATSAPP_MESSAGE_RECEIVED,
                title=title,
                body=push_body,
                data=data,
                lead_source="whatsapp",
            )
    except Exception:
        logger.exception(
            "WhatsApp inbound push failed client_id=%s message_id=%s",
            getattr(client, "id", None),
            message_id,
        )
