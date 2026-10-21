"""
Call recording object storage: the ``recordings`` alias in ``settings.STORAGES``.

R2 (``loop-pbx-recordings``) in production, ``MEDIA_ROOT/pbx_recordings`` on local
disk otherwise. PBX recordings are uploaded by the node itself (CRM-pbx contract
§7); WhatsApp call recordings are saved here by the API. See docs/STORAGE.md.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from django.core.files.base import ContentFile

from object_storage.backends import is_remote, recordings_storage

logger = logging.getLogger(__name__)


def storage():
    return recordings_storage()


def is_object_storage() -> bool:
    return is_remote(storage())


def build_storage_key(
    company_id: int,
    linkedid: str,
    original_name: str,
    *,
    prefix: str = "recording",
) -> str:
    ext = Path(original_name).suffix.lower() or ".wav"
    safe_linked = "".join(c if c.isalnum() or c in ".-_" else "_" for c in linkedid)[:64]
    safe_prefix = "".join(c if c.isalnum() or c in ".-_" else "_" for c in (prefix or "recording"))[:32]
    return f"{safe_prefix}/{company_id}/{safe_linked}/{uuid.uuid4().hex}{ext}"


def save_recording(
    *,
    company_id: int,
    linkedid: str,
    file_bytes: bytes,
    original_filename: str,
    prefix: str = "recording",
) -> str:
    """Persist recording bytes; returns the storage key."""
    key = build_storage_key(company_id, linkedid, original_filename, prefix=prefix)
    content = ContentFile(file_bytes)
    content.content_type = guess_content_type(key)
    saved = storage().save(key, content)
    logger.info("recording.saved key=%s bytes=%s", saved, len(file_bytes))
    return saved


def open_recording(storage_key: str):
    """A readable binary file object for streaming."""
    if not storage_key:
        raise FileNotFoundError("empty storage key")
    store = storage()
    if not is_remote(store) and not store.exists(storage_key):
        raise FileNotFoundError(storage_key)
    return store.open(storage_key, "rb")


def guess_content_type(key: str) -> str:
    ext = Path(key).suffix.lower()
    if ext == ".wav":
        return "audio/wav"
    if ext == ".gsm":
        return "audio/x-gsm"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext in (".webm",):
        return "audio/webm"
    if ext in (".ogg", ".oga"):
        return "audio/ogg"
    if ext == ".m4a":
        return "audio/mp4"
    return "application/octet-stream"
