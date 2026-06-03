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


def _is_dialable_phone(phone: str) -> bool:
    cleaned = (phone or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in ("<unknown>", "unknown", "anonymous", "s", "h", "i"):
        return False
    digits = digits_only(cleaned)
    # Skip bare extensions (e.g. 104) — require at least 7 digits for a real number.
    return len(digits) >= 7


def find_client_by_phone(company, phone: str) -> Optional[Client]:
    """Find a lead by phone number within a company."""
    if not phone or not company or not _is_dialable_phone(phone):
        return None

    keys = phone_match_keys(phone)
    if not keys:
        return None

    for client in Client.objects.filter(company=company).only(
        "id", "phone_number", "name", "assigned_to_id"
    ):
        client_keys = phone_match_keys(client.phone_number or "")
        if keys & client_keys:
            return client

    for row in ClientPhoneNumber.objects.filter(client__company=company).select_related("client"):
        row_keys = phone_match_keys(row.phone_number or "")
        if keys & row_keys:
            return row.client

    return None
