"""
Per-company Lead API key generation and verification.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from django.utils import timezone

KEY_PREFIX_DISPLAY_LEN = 8
KEY_SUFFIX_DISPLAY_LEN = 5
KEY_PUBLIC_PREFIX = "crm_lk_"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def verify_api_key(provided: str, stored_hash: str) -> bool:
    if not provided or not stored_hash:
        return False
    return hmac.compare_digest(hash_api_key(provided), stored_hash)


def generate_lead_api_key() -> tuple[str, str, str, str]:
    """Return (full_key, key_prefix, key_suffix, key_hash). Full key shown once to the user."""
    raw = secrets.token_urlsafe(32)
    full_key = f"{KEY_PUBLIC_PREFIX}{raw}"
    prefix = full_key[:KEY_PREFIX_DISPLAY_LEN]
    suffix = full_key[-KEY_SUFFIX_DISPLAY_LEN:] if len(full_key) > KEY_SUFFIX_DISPLAY_LEN else full_key
    return full_key, prefix, suffix, hash_api_key(full_key)


def extract_lead_api_key_from_request(request) -> str:
    auth = (request.headers.get("Authorization") or request.META.get("HTTP_AUTHORIZATION") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("X-Lead-Api-Key")
        or request.META.get("HTTP_X_LEAD_API_KEY")
        or ""
    ).strip()


def resolve_active_api_key(key: str):
    """Return CompanyLeadApiKey row or None; updates last_used_at on success."""
    from integrations.models import CompanyLeadApiKey

    if not key:
        return None
    key_hash = hash_api_key(key)
    row = (
        CompanyLeadApiKey.objects.select_related("company", "created_by")
        .filter(key_hash=key_hash, is_active=True)
        .first()
    )
    if row:
        CompanyLeadApiKey.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
    return row
