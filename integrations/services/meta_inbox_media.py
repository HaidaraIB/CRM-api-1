"""
Attachment handling for Instagram DM / Messenger webhooks.

Unlike WhatsApp Cloud API there is no media-id → Graph fetch step: the webhook
carries a signed CDN URL directly. That URL expires within hours, so the download
happens inline in the webhook rather than in a background job — by the time a
retry queue drained, the link would usually be dead.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from urllib.parse import urlparse

import requests
from django.conf import settings

from .whatsapp_media import save_bytes_to_message_attachment, validate_whatsapp_upload

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 60
DOWNLOAD_ATTEMPTS = 2

# Meta attachment payload type → SocialMessage.AttachmentKind
_ATTACHMENT_KIND_MAP = {
    'image': 'image',
    'video': 'video',
    'audio': 'audio',
    'file': 'document',
    'share': 'share',
    'story_mention': 'story_mention',
    'ig_reel': 'reel',
    'reel': 'reel',
    'location': 'location',
    # A sticker is just an image as far as rendering is concerned.
    'sticker': 'image',
    'template': 'document',
    'fallback': 'document',
}

# Kinds whose bytes we mirror into our own storage. `share`/`story_mention`/`reel`
# stay as URLs: they point at Instagram content the business does not own, and
# mirroring them would grow storage without a retention policy to bound it.
_DOWNLOADABLE_KINDS = frozenset({'image', 'video', 'audio', 'document'})


def max_media_bytes() -> int:
    return int(getattr(settings, 'META_INBOX_MAX_MEDIA_BYTES', 25 * 1024 * 1024))


def attachment_kind_for(meta_type: str | None) -> str | None:
    return _ATTACHMENT_KIND_MAP.get((meta_type or '').strip().lower())


def _filename_from_url(url: str, mime: str, kind: str) -> str:
    """Meta CDN URLs carry a real filename surprisingly often; fall back on the mime."""
    try:
        path = urlparse(url).path
        name = os.path.basename(path)
        if name and '.' in name:
            return name[:200]
    except Exception:
        pass
    ext = mimetypes.guess_extension(mime or '') or ''
    return f"{kind}{ext or '.bin'}"


def download_attachment(url: str) -> tuple[bytes, str] | None:
    """
    Fetch an attachment from Meta's CDN.

    Returns (bytes, mime) or None. Streams and aborts past the size cap so a
    hostile or oversized file cannot exhaust memory.
    """
    if not url:
        return None

    limit = max_media_bytes()
    last_error = None

    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            with requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as response:
                if not response.ok:
                    last_error = f"HTTP {response.status_code}"
                    continue

                declared = response.headers.get('Content-Length')
                if declared and declared.isdigit() and int(declared) > limit:
                    logger.warning(
                        "Meta Inbox: attachment declared %s bytes, over the %s cap — skipped",
                        declared,
                        limit,
                    )
                    return None

                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        logger.warning(
                            "Meta Inbox: attachment exceeded the %s byte cap mid-stream — skipped",
                            limit,
                        )
                        return None
                    chunks.append(chunk)

                mime = (response.headers.get('Content-Type') or '').split(';')[0].strip()
                return b''.join(chunks), mime
        except Exception as exc:
            last_error = str(exc)

    logger.warning(
        "Meta Inbox: attachment download failed after %s attempts: %s",
        DOWNLOAD_ATTEMPTS,
        last_error,
    )
    return None


# Instagram's outbound limits are tighter than WhatsApp's, so the shared
# validator is wrapped rather than replaced.
_SEND_LIMITS = {
    'image': 8 * 1024 * 1024,
    'video': 25 * 1024 * 1024,
    'audio': 25 * 1024 * 1024,
    'document': 25 * 1024 * 1024,
}


def social_kind_for_upload(uploaded, *, requested: str | None = None) -> str:
    """Best-effort attachment kind for an outbound upload."""
    if requested in _SEND_LIMITS:
        return requested
    content_type = (getattr(uploaded, 'content_type', '') or '').lower()
    if content_type.startswith('image/'):
        return 'image'
    if content_type.startswith('video/'):
        return 'video'
    if content_type.startswith('audio/'):
        return 'audio'
    return 'document'


def validate_social_upload(uploaded, *, kind: str | None = None) -> str | None:
    """Returns an error_key when the file cannot be sent, else None."""
    if not uploaded or not getattr(uploaded, 'size', None):
        return 'social_media_empty'

    resolved = social_kind_for_upload(uploaded, requested=kind)
    limit = min(_SEND_LIMITS.get(resolved, 25 * 1024 * 1024), max_media_bytes())
    if int(uploaded.size) > limit:
        return 'social_media_too_large'

    try:
        # Reuse the shared type allow-list; only the size ceiling differs.
        validate_whatsapp_upload(uploaded)
    except ValueError:
        return 'social_media_type_not_allowed'
    except Exception:
        logger.exception("Meta Inbox: upload validation raised unexpectedly")
        return 'social_media_type_not_allowed'
    return None


def apply_attachment_to_message(msg, attachment: dict) -> bool:
    """
    Populate a SocialMessage from one Meta attachment object.

    Always records attachment_kind and source_media_url, even when the download
    fails, so the thread still renders something and the URL is available for a
    manual retry while it is alive. Returns True when bytes were stored.

    Never raises: a failed attachment must not cost the message row.
    """
    if not isinstance(attachment, dict):
        return False

    meta_type = attachment.get('type')
    kind = attachment_kind_for(meta_type)
    payload = attachment.get('payload') if isinstance(attachment.get('payload'), dict) else {}
    url = (payload.get('url') or '').strip()

    if kind:
        msg.attachment_kind = kind
    if url:
        msg.source_media_url = url

    if kind == 'location':
        coords = payload.get('coordinates') if isinstance(payload.get('coordinates'), dict) else {}
        if coords.get('lat') is not None:
            msg.location_latitude = coords.get('lat')
        if coords.get('long') is not None:
            msg.location_longitude = coords.get('long')
        msg.location_name = (payload.get('title') or '')[:255]
        return False

    if not url or kind not in _DOWNLOADABLE_KINDS:
        return False

    try:
        result = download_attachment(url)
        if not result:
            return False
        data, mime = result
        filename = _filename_from_url(url, mime, kind)
        save_bytes_to_message_attachment(msg, data, filename, mime)
        return True
    except Exception:
        logger.exception("Meta Inbox: unexpected failure storing attachment")
        return False
