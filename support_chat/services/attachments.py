"""Reuse tenant chat attachment validation and storage with support_chat paths."""

from __future__ import annotations

from tenant_chat import supabase_storage as chat_storage
from tenant_chat.attachments import (
    KIND_IMAGE,
    image_upload_pixel_dimensions,
    media_preview_label,
    message_has_stored_attachment,
    normalize_chat_image_upload,
    safe_original_filename,
    validate_uploaded_file,
)

STORAGE_PATH_PREFIX = "support_chat"

__all__ = [
    "KIND_IMAGE",
    "chat_storage",
    "image_upload_pixel_dimensions",
    "media_preview_label",
    "message_has_stored_attachment",
    "normalize_chat_image_upload",
    "safe_original_filename",
    "validate_uploaded_file",
    "build_object_key",
    "is_supabase_chat_storage",
    "is_supabase_mode_requested",
    "is_configured",
]


def build_object_key(company_id: int, message_id: int, filename_hint: str) -> str:
    return chat_storage.build_object_key(
        company_id,
        message_id,
        filename_hint,
        path_prefix=STORAGE_PATH_PREFIX,
    )


def is_supabase_chat_storage() -> bool:
    return chat_storage.is_supabase_chat_storage()


def is_supabase_mode_requested() -> bool:
    return chat_storage.is_supabase_mode_requested()


def is_configured() -> bool:
    return chat_storage.is_configured()
