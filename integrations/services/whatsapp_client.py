"""Ensure CRM clients exist for WhatsApp threads (inbound webhook + outbound send)."""

from __future__ import annotations

import logging
from typing import Optional

from django.utils import timezone

from crm.models import Client, ClientEvent, ClientPhoneNumber
from integrations.services.phone_match import find_client_by_phone

logger = logging.getLogger(__name__)


def ensure_client_for_whatsapp_phone(company, phone: str, integration_account=None) -> Optional[Client]:
    """
    Find or create a CRM client for a WhatsApp phone number within a company.
    Used when sending outbound messages and when processing inbound webhooks.
    """
    if not company or not phone:
        return None

    client = find_client_by_phone(company, phone)
    if client:
        return client

    normalized = (phone or "").strip()
    if not normalized:
        return None

    client = Client.objects.create(
        name=f"WhatsApp: {normalized}",
        priority="medium",
        type="fresh",
        company=company,
        source="whatsapp",
        integration_account=integration_account,
        phone_number=normalized,
    )
    ClientPhoneNumber.objects.create(
        client=client,
        phone_number=normalized,
        phone_type="mobile",
        is_primary=True,
    )
    ClientEvent.objects.create(
        client=client,
        event_type="created",
        new_value="WhatsApp",
        notes="Client created for WhatsApp conversation",
    )
    logger.info("Created WhatsApp client company_id=%s client_id=%s", company.id, client.id)
    return client


def touch_client_last_contacted(client: Client) -> None:
    client.last_contacted_at = timezone.now()
    client.save(update_fields=["last_contacted_at"])
