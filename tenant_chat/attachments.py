"""
Tenant chat attachment validation, size limits, and optional server-side image normalization.
"""

from __future__ import annotations

import os
from io import BytesIO
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

# --- Max upload sizes (bytes) ---
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 32 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
# After Pillow normalization, refuse if still above this (rare).
MAX_IMAGE_POST_NORMALIZE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_LONG_EDGE = 2048

KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_DOCUMENT = "document"

ALLOWED_IMAGE_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)
ALLOWED_VIDEO_TYPES = frozenset(
    {"video/mp4", "video/webm", "video/quicktime"}
)
ALLOWED_AUDIO_TYPES = frozenset(
    {
        "audio/webm",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/ogg",
        "audio/opus",
    }
)
ALLOWED_DOCUMENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)

_EXT_KIND = {
    ".jpg": KIND_IMAGE,
    ".jpeg": KIND_IMAGE,
    ".png": KIND_IMAGE,
    ".webp": KIND_IMAGE,
    ".gif": KIND_IMAGE,
    ".mp4": KIND_VIDEO,
    ".webm": KIND_VIDEO,
    ".mov": KIND_VIDEO,
    ".mp3": KIND_AUDIO,
    ".wav": KIND_AUDIO,
    ".ogg": KIND_AUDIO,
    ".m4a": KIND_AUDIO,
    ".pdf": KIND_DOCUMENT,
    ".doc": KIND_DOCUMENT,
    ".docx": KIND_DOCUMENT,
    ".xls": KIND_DOCUMENT,
    ".xlsx": KIND_DOCUMENT,
}


def _kind_max_bytes(kind: str) -> int:
    return {
        KIND_IMAGE: MAX_IMAGE_BYTES,
        KIND_VIDEO: MAX_VIDEO_BYTES,
        KIND_AUDIO: MAX_AUDIO_BYTES,
        KIND_DOCUMENT: MAX_DOCUMENT_BYTES,
    }[kind]


def infer_attachment_kind(content_type: str | None, filename: str | None) -> str | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_IMAGE_TYPES:
        return KIND_IMAGE
    if ct in ALLOWED_VIDEO_TYPES:
        return KIND_VIDEO
    if ct in ALLOWED_AUDIO_TYPES:
        return KIND_AUDIO
    if ct in ALLOWED_DOCUMENT_TYPES:
        return KIND_DOCUMENT
    name = (filename or "").lower()
    ext = os.path.splitext(name)[1]
    return _EXT_KIND.get(ext)


def _default_mime_for_filename(name: str) -> str | None:
    ext = os.path.splitext((name or "").lower())[1]
    defaults = {
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
    return defaults.get(ext)


def validate_uploaded_file(uploaded: UploadedFile) -> tuple[str, str, int]:
    """
    Returns (kind, normalized_content_type, size_bytes).
    Raises ValueError with user-facing message on rejection.
    """
    if not uploaded or not getattr(uploaded, "size", None):
        raise ValueError("Empty file.")
    size = int(uploaded.size)
    name = getattr(uploaded, "name", "") or ""
    raw_ct = getattr(uploaded, "content_type", None) or ""
    kind = infer_attachment_kind(raw_ct, name)
    if not kind:
        raise ValueError(
            f"File type '{raw_ct or 'unknown'}' is not allowed for team chat attachments."
        )
    max_b = _kind_max_bytes(kind)
    if size > max_b:
        raise ValueError(f"File exceeds the maximum size of {max_b // (1024 * 1024)} MB for this type.")

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
        inferred = _default_mime_for_filename(name)
        if inferred and inferred in allowed:
            ct = inferred
        elif kind == KIND_VIDEO and (ct == "application/octet-stream" or not ct):
            # Browser may label webm/mp4 oddly; extension already fixed kind
            ext = os.path.splitext(name.lower())[1]
            ct = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}.get(ext)
            if not ct or ct not in allowed:
                raise ValueError("Could not determine file type; try a different format.")
        else:
            raise ValueError(f"File type '{raw_ct or 'unknown'}' is not allowed for team chat attachments.")
    else:
        raise ValueError(f"File type '{raw_ct}' is not allowed for team chat attachments.")

    return kind, ct, size


def normalize_chat_image_upload(uploaded: UploadedFile) -> tuple[ContentFile, str, int] | None:
    """
    Raster images only. Returns (ContentFile, mime, size) or None to use original bytes (e.g. GIF).
    """
    raw_ct = (getattr(uploaded, "content_type", None) or "").split(";")[0].strip().lower()
    if raw_ct == "image/gif":
        return None

    from PIL import Image, ImageOps

    uploaded.seek(0)
    img = Image.open(uploaded)
    img.load()
    img = ImageOps.exif_transpose(img)

    w, h = img.size
    if max(w, h) > MAX_IMAGE_LONG_EDGE:
        ratio = MAX_IMAGE_LONG_EDGE / float(max(w, h))
        new_w = max(1, int(w * ratio))
        new_h = max(1, int(h * ratio))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    buf = BytesIO()
    out_mime = "image/webp"
    try:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.save(buf, format="WEBP", quality=85, method=4)
    except Exception:
        buf = BytesIO()
        img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=85, optimize=True)
        out_mime = "image/jpeg"

    data = buf.getvalue()
    if len(data) > MAX_IMAGE_POST_NORMALIZE_BYTES:
        raise ValueError("Image is still too large after processing; try a smaller image.")

    out = ContentFile(data)
    out.name = "chat.webp" if out_mime == "image/webp" else "chat.jpg"
    return out, out_mime, len(data)


def safe_original_filename(name: str | None, max_len: int = 200) -> str:
    base = os.path.basename(name or "") or "attachment"
    base = base.replace("\x00", "").strip()
    if len(base) > max_len:
        root, ext = os.path.splitext(base)
        base = root[: max_len - len(ext) - 1] + ext
    return base or "attachment"


def message_has_stored_attachment(msg) -> bool:
    key = getattr(msg, "attachment_object_key", None) or ""
    if str(key).strip():
        return True
    att = getattr(msg, "attachment", None)
    return bool(att and getattr(att, "name", None))


def copy_chat_attachment_from_source(src, dst) -> None:
    """Duplicate stored file and metadata from src onto dst (e.g. forward)."""
    from django.core.files.base import ContentFile

    from . import supabase_storage as sc

    if sc.is_supabase_chat_storage():
        dst.attachment_kind = src.attachment_kind
        dst.attachment_mime = src.attachment_mime or ""
        dst.attachment_size = src.attachment_size
        dst.original_filename = src.original_filename or ""

        data: bytes | None = None
        if getattr(src, "attachment_object_key", None):
            data = sc.download_bytes(src.attachment_object_key)
        elif src.attachment:
            src.attachment.open("rb")
            try:
                data = src.attachment.read()
            finally:
                src.attachment.close()
        if data is None:
            return

        key = sc.build_object_key(dst.conversation.company_id, dst.id, dst.original_filename or "fwd")
        sc.upload_bytes(key, data, dst.attachment_mime or "application/octet-stream")
        dst.attachment_object_key = key
        dst.save(
            update_fields=[
                "attachment_kind",
                "attachment_mime",
                "attachment_size",
                "original_filename",
                "attachment_object_key",
            ]
        )
        return

    if not src.attachment:
        return
    name = os.path.basename(src.attachment.name)
    src.attachment.open("rb")
    try:
        data = src.attachment.read()
    finally:
        src.attachment.close()
    dst.attachment_kind = src.attachment_kind
    dst.attachment_mime = src.attachment_mime
    dst.attachment_size = src.attachment_size
    dst.original_filename = src.original_filename
    dst.attachment.save(name, ContentFile(data), save=True)


def media_preview_label(kind: str | None, body: str | None) -> str:
    """Short label for notifications / last-message preview."""
    labels = {
        KIND_IMAGE: "Photo",
        KIND_VIDEO: "Video",
        KIND_AUDIO: "Voice message",
        KIND_DOCUMENT: "Document",
    }
    base = labels.get(kind or "", "")
    cap = (body or "").strip().replace("\n", " ")
    if base and cap:
        return f"{base}: {cap[:120]}"
    if cap:
        return cap[:160]
    return base or "Message"
