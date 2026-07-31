"""Match inbound/outbound phone numbers to CRM leads."""

from __future__ import annotations

import re
from typing import Optional

from crm.models import Client, ClientPhoneNumber
from integrations.services.twilio_phone import normalize_phone_to_e164


def digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


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


def canonical_phone_key(phone: str) -> str:
    """
    Digits-only E.164 key used for company-wide uniqueness on ClientPhoneNumber.
    Empty string means the value is not dialable / not enforceable.
    """
    if not phone or not _is_dialable_phone(phone):
        return ""
    return digits_only(normalize_phone_to_e164(phone))


def phone_match_keys(phone: str) -> set[str]:
    """Return normalized keys for fuzzy lead matching (Iraq 07… vs +964…)."""
    raw = (phone or "").strip()
    raw_digits = digits_only(raw)
    e164 = normalize_phone_to_e164(raw)
    digits = digits_only(e164)
    keys: set[str] = {e164, digits}
    if raw_digits:
        keys.add(raw_digits)

    # Iraq local 07XXXXXXXXX <-> E.164 +9647XXXXXXXX
    if raw_digits.startswith("0") and len(raw_digits) >= 10:
        keys.add("964" + raw_digits[1:])
    if digits.startswith("964") and len(digits) > 3:
        national = digits[3:]
        keys.add(national)
        if national and not national.startswith("0"):
            keys.add("0" + national)

    if len(digits) >= 9:
        keys.add(digits[-9:])
    if len(digits) >= 10:
        keys.add(digits[-10:])
    return {k for k in keys if k}


def find_client_by_phone(company, phone: str, prefer_assigned_to=None) -> Optional[Client]:
    """
    Find a lead by phone number within a company.

    When multiple leads share a matching phone (legacy duplicates), prefer:
    1. Lead assigned to ``prefer_assigned_to`` (if given)
    2. Otherwise first match (iteration order)
    """
    if not phone or not company or not _is_dialable_phone(phone):
        return None

    key = canonical_phone_key(phone)
    if key:
        row = (
            ClientPhoneNumber.objects.filter(company=company, phone_normalized=key)
            .select_related("client")
            .first()
        )
        if row is not None:
            return row.client

        # Primary field only (no ClientPhoneNumber row yet)
        for client in Client.objects.filter(company=company).only(
            "id", "phone_number", "name", "assigned_to_id"
        ):
            if canonical_phone_key(client.phone_number or "") == key:
                return client

    keys = phone_match_keys(phone)
    if not keys:
        return None

    matches: list[Client] = []
    seen_ids: set[int] = set()

    for client in Client.objects.filter(company=company).only(
        "id", "phone_number", "name", "assigned_to_id"
    ):
        client_keys = phone_match_keys(client.phone_number or "")
        if keys & client_keys and client.id not in seen_ids:
            matches.append(client)
            seen_ids.add(client.id)

    for row in ClientPhoneNumber.objects.filter(client__company=company).select_related("client"):
        row_keys = phone_match_keys(row.phone_number or "")
        if keys & row_keys and row.client_id not in seen_ids:
            matches.append(row.client)
            seen_ids.add(row.client_id)

    if not matches:
        return None

    if prefer_assigned_to is not None:
        prefer_id = getattr(prefer_assigned_to, "id", prefer_assigned_to)
        for client in matches:
            if getattr(client, "assigned_to_id", None) == prefer_id:
                return client

    return matches[0]
