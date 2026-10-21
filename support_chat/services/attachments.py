"""Reuse tenant chat attachment validation and storage with support_chat paths."""

from __future__ import annotations

from tenant_chat.attachments import (
    KIND_IMAGE,
    image_upload_pixel_dimensions,
    media_preview_label,
    message_has_stored_attachment,
    normalize_chat_image_upload,
    safe_original_filename,
    validate_uploaded_file,
)

__all__ = [
    "KIND_IMAGE",
    "image_upload_pixel_dimensions",
    "media_preview_label",
    "message_has_stored_attachment",
    "normalize_chat_image_upload",
    "safe_original_filename",
    "validate_uploaded_file",
]
