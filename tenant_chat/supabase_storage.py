"""
Supabase Storage (S3-compatible REST) for tenant chat attachments.

Uses ``requests`` only (no supabase-py). Configure via env / Django settings:
``TENANT_CHAT_STORAGE=supabase``, ``SUPABASE_URL``, ``SUPABASE_SERVICE_ROLE_KEY``,
``SUPABASE_CHAT_BUCKET``.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(
        getattr(settings, "SUPABASE_URL", "")
        and getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")
        and getattr(settings, "SUPABASE_CHAT_BUCKET", "")
    )


def is_supabase_mode_requested() -> bool:
    mode = getattr(settings, "TENANT_CHAT_STORAGE", "local") or "local"
    return str(mode).strip().lower() == "supabase"


def is_supabase_chat_storage() -> bool:
    if not is_supabase_mode_requested():
        return False
    if not is_configured():
        logger.warning(
            "TENANT_CHAT_STORAGE=supabase but Supabase URL, service role key, or bucket is missing."
        )
        return False
    return True


def _base_url() -> str:
    return (settings.SUPABASE_URL or "").rstrip("/")


def _headers_json() -> dict[str, str]:
    key = settings.SUPABASE_SERVICE_ROLE_KEY or ""
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
        "Content-Type": "application/json",
    }


def _headers_binary(content_type: str) -> dict[str, str]:
    key = settings.SUPABASE_SERVICE_ROLE_KEY or ""
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "true",
    }


def _headers_read() -> dict[str, str]:
    key = settings.SUPABASE_SERVICE_ROLE_KEY or ""
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
    }


def encode_object_path(object_key: str) -> str:
    key = (object_key or "").strip().lstrip("/")
    return "/".join(quote(part, safe="") for part in key.split("/") if part)


def build_object_key(company_id: int, message_id: int, filename_hint: str) -> str:
    base = os.path.basename(filename_hint) or "file"
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._") or "file"
    if len(base) > 120:
        root, ext = os.path.splitext(base)
        base = root[: 120 - len(ext)] + ext
    return f"company_{company_id}/m{message_id}_{uuid.uuid4().hex[:10]}_{base}"


def upload_bytes(object_key: str, data: bytes, content_type: str) -> None:
    bucket = settings.SUPABASE_CHAT_BUCKET
    enc = encode_object_path(object_key)
    url = f"{_base_url()}/storage/v1/object/{quote(bucket, safe='')}/{enc}"
    r = requests.post(url, data=data, headers=_headers_binary(content_type), timeout=120)
    if r.status_code not in (200, 201):
        logger.error("Supabase upload failed %s %s: %s", r.status_code, object_key, r.text[:500])
        r.raise_for_status()


def download_bytes(object_key: str) -> bytes:
    bucket = settings.SUPABASE_CHAT_BUCKET
    enc = encode_object_path(object_key)
    url = f"{_base_url()}/storage/v1/object/{quote(bucket, safe='')}/{enc}"
    r = requests.get(url, headers=_headers_read(), timeout=120)
    if r.status_code != 200:
        logger.error("Supabase download failed %s %s: %s", r.status_code, object_key, r.text[:500])
        r.raise_for_status()
    return r.content


def create_signed_url(object_key: str, expires_in: int = 3600) -> str:
    bucket = settings.SUPABASE_CHAT_BUCKET
    enc = encode_object_path(object_key)
    url = f"{_base_url()}/storage/v1/object/sign/{quote(bucket, safe='')}/{enc}"
    r = requests.post(
        url,
        json={"expiresIn": int(expires_in)},
        headers=_headers_json(),
        timeout=30,
    )
    if r.status_code != 200:
        logger.error("Supabase sign failed %s %s: %s", r.status_code, object_key, r.text[:500])
        r.raise_for_status()
    data = r.json()
    signed = data.get("signedURL") or data.get("signedUrl") or data.get("signed_url")
    if not signed or not isinstance(signed, str):
        raise ValueError("Unexpected sign response from Supabase Storage")
    signed = signed.strip()
    if signed.startswith("http://") or signed.startswith("https://"):
        return signed
    root = _base_url()
    if signed.startswith("/"):
        return f"{root}{signed}"
    return f"{root}/{signed}"
