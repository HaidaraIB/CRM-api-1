"""WhatsApp template header media: storage helpers, Meta resumable upload, send components."""
from __future__ import annotations

import logging
import mimetypes
import os
from typing import Optional

import requests
from django.conf import settings

from integrations.oauth_utils import META_GRAPH_API_BASE_URL
from integrations.services.whatsapp_media import (
    KIND_DOCUMENT,
    KIND_IMAGE,
    KIND_VIDEO,
    save_bytes_to_message_attachment,
    upload_media_to_meta,
    validate_whatsapp_upload,
)

logger = logging.getLogger(__name__)

MEDIA_HEADER_TYPES = frozenset({"image", "video", "document"})

_HEADER_KIND = {
    "image": KIND_IMAGE,
    "video": KIND_VIDEO,
    "document": KIND_DOCUMENT,
}

_META_HEADER_FORMAT = {
    "image": "IMAGE",
    "video": "VIDEO",
    "document": "DOCUMENT",
}

_META_PARAM_TYPE = {
    "image": "image",
    "video": "video",
    "document": "document",
}


def template_header_type(template) -> str:
    return (getattr(template, "header_type", None) or "").strip().lower()


def template_requires_header_media(template) -> bool:
    return template_header_type(template) in MEDIA_HEADER_TYPES


def template_has_header_media(template) -> bool:
    field = getattr(template, "header_media", None)
    return bool(field and getattr(field, "name", None))


def template_header_attachment_kind(template) -> str | None:
    ht = template_header_type(template)
    if ht in MEDIA_HEADER_TYPES:
        return ht
    return None


def expected_kind_for_header_type(header_type: str) -> str | None:
    return _HEADER_KIND.get((header_type or "").strip().lower())


def read_template_header_bytes(template) -> tuple[bytes, str, str]:
    """Return (bytes, mime, filename) for a stored template header media file."""
    field = getattr(template, "header_media", None)
    if not field or not getattr(field, "name", None):
        raise ValueError("Template has no header media file.")
    mime = (getattr(template, "header_media_mime", None) or "").strip()
    if not mime:
        mime = mimetypes.guess_type(field.name)[0] or "application/octet-stream"
    with field.open("rb") as handle:
        data = handle.read()
    filename = os.path.basename(field.name) or "header"
    return data, mime, filename


def upload_template_header_handle(
    *,
    app_id: str,
    access_token: str,
    data: bytes,
    mime: str,
) -> str:
    """Upload header bytes via Meta Resumable Upload API; return header_handle for template creation."""
    app_id = str(app_id or "").strip()
    if not app_id:
        raise ValueError("Meta app id is not configured.")
    if not data:
        raise ValueError("Empty header media.")
    file_type = (mime or "application/octet-stream").split(";")[0].strip()
    start_url = f"{META_GRAPH_API_BASE_URL}/{app_id}/uploads"
    headers = {"Authorization": f"Bearer {access_token}"}
    start_resp = requests.post(
        start_url,
        params={"file_length": len(data), "file_type": file_type},
        headers=headers,
        timeout=30,
    )
    if start_resp.status_code >= 400:
        logger.warning(
            "Meta template header upload session failed: status=%s body=%s",
            start_resp.status_code,
            start_resp.text[:500],
        )
        raise ValueError("Meta template header upload failed.")
    session_id = (start_resp.json() or {}).get("id")
    if not session_id:
        raise ValueError("Meta template header upload returned no session id.")
    upload_url = f"{META_GRAPH_API_BASE_URL}/{session_id}"
    upload_headers = {
        "Authorization": f"OAuth {access_token}",
        "file_offset": "0",
        "Content-Type": "application/octet-stream",
    }
    upload_resp = requests.post(
        upload_url,
        headers=upload_headers,
        data=data,
        timeout=120,
    )
    if upload_resp.status_code >= 400:
        logger.warning(
            "Meta template header binary upload failed: status=%s body=%s",
            upload_resp.status_code,
            upload_resp.text[:500],
        )
        raise ValueError("Meta template header upload failed.")
    handle = (upload_resp.json() or {}).get("h")
    if not handle:
        raise ValueError("Meta template header upload returned no handle.")
    return str(handle)


def build_meta_submit_header_component(
    template,
    *,
    app_id: str,
    access_token: str,
) -> dict:
    """Build Meta message_templates HEADER component for image/video/document."""
    ht = template_header_type(template)
    fmt = _META_HEADER_FORMAT.get(ht)
    if not fmt:
        raise ValueError("Template header type does not use media.")
    if not template_has_header_media(template):
        raise ValueError("Template header media is required.")
    data, mime, _filename = read_template_header_bytes(template)
    handle = upload_template_header_handle(
        app_id=app_id,
        access_token=access_token,
        data=data,
        mime=mime,
    )
    return {
        "type": "HEADER",
        "format": fmt,
        "example": {"header_handle": [handle]},
    }


def build_meta_send_header_component(
    template,
    *,
    phone_number_id: str,
    access_token: str,
) -> dict | None:
    """Build Meta send-time template header component with uploaded media id."""
    ht = template_header_type(template)
    param_type = _META_PARAM_TYPE.get(ht)
    if not param_type:
        return None
    if not template_has_header_media(template):
        return None
    data, mime, filename = read_template_header_bytes(template)
    media_id = upload_media_to_meta(
        phone_number_id=phone_number_id,
        access_token=access_token,
        data=data,
        mime=mime,
        filename=filename,
    )
    media_obj = {"id": media_id}
    return {
        "type": "header",
        "parameters": [{"type": param_type, param_type: media_obj}],
    }


def attach_template_header_to_message(msg, template) -> None:
    """Copy template header media onto an outbound LeadWhatsAppMessage for chat display."""
    kind = template_header_attachment_kind(template)
    if not kind or not template_has_header_media(template):
        return
    try:
        data, mime, filename = read_template_header_bytes(template)
    except ValueError:
        return
    msg.attachment_kind = kind
    save_bytes_to_message_attachment(msg, data, filename, mime)
    msg.save(
        update_fields=[
            "attachment",
            "attachment_kind",
            "attachment_mime",
            "attachment_size",
            "original_filename",
        ]
    )


def validate_header_media_upload(uploaded, header_type: str) -> tuple[str, str]:
    """Validate uploaded header file matches header_type; return (kind, mime)."""
    kind, mime, _size = validate_whatsapp_upload(uploaded, want_voice=False)
    expected = expected_kind_for_header_type(header_type)
    if expected and kind != expected:
        raise ValueError(
            f"Header media must be a {expected} file for this header type."
        )
    return kind, mime


def meta_app_id() -> str:
    return str(getattr(settings, "WHATSAPP_CLIENT_ID", "") or "").strip()
