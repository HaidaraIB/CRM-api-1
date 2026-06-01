"""Match inbound/outbound phone numbers to CRM leads."""

from __future__ import annotations

import re
from typing import Optional

from crm.models import Client, ClientPhoneNumber
from integrations.services.twilio_phone import normalize_phone_to_e164


def digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def phone_match_keys(phone: str) -> set[str]:
    """Return normalized keys for fuzzy lead matching."""
    e164 = normalize_phone_to_e164(phone)
    digits = digits_only(e164)
    keys = {e164, digits}
    if len(digits) >= 9:
        keys.add(digits[-9:])
    if len(digits) >= 10:
        keys.add(digits[-10:])
    return {k for k in keys if k}


def find_client_by_phone(company, phone: str) -> Optional[Client]:
    """Find a lead by phone number within a company."""
    if not phone or not company:
        return None

    keys = phone_match_keys(phone)
    if not keys:
        return None

    # Primary client.phone field
    for client in Client.objects.filter(company=company).only("id", "phone", "name", "assigned_to_id"):
        client_keys = phone_match_keys(client.phone or "")
        if keys & client_keys:
            return client

    # ClientPhoneNumber rows
    for row in ClientPhoneNumber.objects.filter(client__company=company).select_related("client"):
        row_keys = phone_match_keys(row.phone_number or "")
        if keys & row_keys:
            return row.client

    return None
