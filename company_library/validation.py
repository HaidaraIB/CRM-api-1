"""Validate company library uploads (type allowlist + plan file-size quota)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from tenant_chat.attachments import (
    ALLOWED_AUDIO_TYPES,
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_IMAGE_TYPES,
    ALLOWED_VIDEO_TYPES,
    KIND_AUDIO,
    KIND_DOCUMENT,
    KIND_IMAGE,
    KIND_VIDEO,
    infer_attachment_kind,
    safe_original_filename,
)

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile


_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}


def validate_library_upload(uploaded: UploadedFile) -> tuple[str, str, int, str]:
    """
    Returns (kind, mime_type, size_bytes, original_filename).
    Raises ValueError with a user-facing message on rejection.
    Size caps are enforced separately via plan quotas.
    """
    if not uploaded or not getattr(uploaded, "size", None):
        raise ValueError("Empty file.")
    size = int(uploaded.size)
    name = safe_original_filename(getattr(uploaded, "name", "") or "")
    raw_ct = getattr(uploaded, "content_type", None) or ""
    kind = infer_attachment_kind(raw_ct, name)
    if not kind:
        raise ValueError(
            f"File type '{raw_ct or 'unknown'}' is not allowed for the company library."
        )

    ct = raw_ct.split(";")[0].strip().lower() if raw_ct else ""
    allowed = {
        KIND_IMAGE: ALLOWED_IMAGE_TYPES,
        KIND_VIDEO: ALLOWED_VIDEO_TYPES,
        KIND_AUDIO: ALLOWED_AUDIO_TYPES,
        KIND_DOCUMENT: ALLOWED_DOCUMENT_TYPES,
    }[kind]

    if ct in allowed:
        pass
    elif not ct or ct == "application/octet-stream":
        inferred = _MIME_BY_EXT.get(os.path.splitext(name.lower())[1])
        if inferred and inferred in allowed:
            ct = inferred
        else:
            raise ValueError(
                f"File type '{raw_ct or 'unknown'}' is not allowed for the company library."
            )
    else:
        raise ValueError(f"File type '{raw_ct}' is not allowed for the company library.")

    return kind, ct, size, name
