"""
WhatsApp Cloud API media upload/download + MIME policy.

Voice notes require audio/ogg;codecs=opus. Non-OGG browser recordings (webm, mp4,
aac, …) are converted via ffmpeg when available; otherwise outbound voice send
fails with a clear error.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

import requests
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile

from integrations.oauth_utils import META_GRAPH_API_BASE_URL

logger = logging.getLogger(__name__)

VOICE_NOTE_CONVERT_ERROR_CODE = "whatsapp_voice_note_requires_ogg"
_VOICE_CONVERT_ERROR = (
    "Voice notes require OGG/Opus. Install ffmpeg on the server "
    "or record in a browser that supports audio/ogg."
)


def is_voice_note_convert_error(exc: BaseException) -> bool:
    return isinstance(exc, ValueError) and str(exc) == _VOICE_CONVERT_ERROR


# Cloud API practical limits (bytes) — see Meta media docs.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VIDEO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024

KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_DOCUMENT = "document"

ALLOWED_IMAGE = frozenset({"image/jpeg", "image/png", "image/webp"})
ALLOWED_VIDEO = frozenset({"video/mp4", "video/3gpp"})
ALLOWED_AUDIO = frozenset(
    {
        "audio/aac",
        "audio/mp4",
        "audio/mpeg",
        "audio/amr",
        "audio/ogg",
        "audio/opus",
        # Accepted for ingest; converted before Meta voice upload when possible.
        "audio/webm",
    }
)
ALLOWED_DOCUMENT = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
    }
)

_EXT_KIND = {
    ".jpg": KIND_IMAGE,
    ".jpeg": KIND_IMAGE,
    ".png": KIND_IMAGE,
    ".webp": KIND_IMAGE,
    ".mp4": KIND_VIDEO,
    ".3gp": KIND_VIDEO,
    ".mp3": KIND_AUDIO,
    ".m4a": KIND_AUDIO,
    ".aac": KIND_AUDIO,
    ".amr": KIND_AUDIO,
    ".ogg": KIND_AUDIO,
    ".opus": KIND_AUDIO,
    ".webm": KIND_AUDIO,
    ".pdf": KIND_DOCUMENT,
    ".doc": KIND_DOCUMENT,
    ".docx": KIND_DOCUMENT,
    ".xls": KIND_DOCUMENT,
    ".xlsx": KIND_DOCUMENT,
    ".txt": KIND_DOCUMENT,
}


def _kind_max(kind: str) -> int:
    return {
        KIND_IMAGE: MAX_IMAGE_BYTES,
        KIND_VIDEO: MAX_VIDEO_BYTES,
        KIND_AUDIO: MAX_AUDIO_BYTES,
        KIND_DOCUMENT: MAX_DOCUMENT_BYTES,
    }[kind]


def infer_kind(content_type: str | None, filename: str | None) -> str | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_IMAGE:
        return KIND_IMAGE
    if ct in ALLOWED_VIDEO:
        return KIND_VIDEO
    if ct in ALLOWED_AUDIO or ct.startswith("audio/"):
        if ct in ALLOWED_AUDIO or ct.startswith("audio/"):
            # Treat unknown audio/* as audio if extension says so
            if ct in ALLOWED_AUDIO:
                return KIND_AUDIO
    if ct in ALLOWED_DOCUMENT:
        return KIND_DOCUMENT
    ext = os.path.splitext((filename or "").lower())[1]
    return _EXT_KIND.get(ext)


def validate_whatsapp_upload(
    uploaded: UploadedFile, *, want_voice: bool = False
) -> tuple[str, str, int]:
    """Return (kind, mime, size). Raises ValueError with user-facing message."""
    if not uploaded or not getattr(uploaded, "size", None):
        raise ValueError("Empty file.")
    size = int(uploaded.size)
    name = getattr(uploaded, "name", "") or ""
    raw_ct = (getattr(uploaded, "content_type", None) or "").split(";")[0].strip().lower()
    kind = infer_kind(raw_ct, name)
    if not kind:
        raise ValueError(f"File type '{raw_ct or 'unknown'}' is not allowed for WhatsApp.")
    if size > _kind_max(kind):
        raise ValueError(
            f"File exceeds the maximum size of {_kind_max(kind) // (1024 * 1024)} MB for WhatsApp."
        )
    ct = raw_ct
    allowed = {
        KIND_IMAGE: ALLOWED_IMAGE,
        KIND_VIDEO: ALLOWED_VIDEO,
        KIND_AUDIO: ALLOWED_AUDIO,
        KIND_DOCUMENT: ALLOWED_DOCUMENT,
    }[kind]
    if not ct or ct == "application/octet-stream":
        ext = os.path.splitext(name.lower())[1]
        defaults = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".mp4": "video/mp4",
            ".webm": "audio/webm",
            ".ogg": "audio/ogg",
            ".opus": "audio/ogg",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".aac": "audio/aac",
            ".amr": "audio/amr",
            ".pdf": "application/pdf",
        }
        ct = defaults.get(ext, ct)
    if kind == KIND_AUDIO and ct.startswith("audio/") and ct not in allowed:
        # Allow browser recordings labeled oddly
        if "webm" in ct or name.lower().endswith(".webm"):
            ct = "audio/webm"
        elif "ogg" in ct or "opus" in ct:
            ct = "audio/ogg"
        elif name.lower().endswith((".m4a", ".mp4")) or "mp4" in ct:
            ct = "audio/mp4"
        elif name.lower().endswith(".aac") or "aac" in ct:
            ct = "audio/aac"
    if ct not in allowed and not (kind == KIND_AUDIO and ct.startswith("audio/")):
        raise ValueError(f"File type '{ct or raw_ct}' is not allowed for WhatsApp.")
    if want_voice and kind != KIND_AUDIO:
        raise ValueError("Voice notes must be audio files.")
    return kind, ct or raw_ct or "application/octet-stream", size


def _temp_suffix_for_audio_mime(mime: str, filename: str) -> str:
    """Pick a temp-file suffix so ffmpeg can sniff the container correctly."""
    mime_l = (mime or "").split(";")[0].strip().lower()
    name = (filename or "").lower()
    if "webm" in mime_l or name.endswith(".webm"):
        return ".webm"
    if "ogg" in mime_l or "opus" in mime_l or name.endswith((".ogg", ".opus")):
        return ".ogg"
    if mime_l == "audio/aac" or name.endswith(".aac"):
        return ".aac"
    if "mpeg" in mime_l or name.endswith(".mp3"):
        return ".mp3"
    if "amr" in mime_l or name.endswith(".amr"):
        return ".amr"
    if "mp4" in mime_l or name.endswith((".m4a", ".mp4")):
        return ".m4a"
    ext = os.path.splitext(name)[1]
    return ext if ext else ".bin"


def _is_ogg_opus_mime(mime: str) -> bool:
    mime_l = (mime or "").split(";")[0].strip().lower()
    return "ogg" in mime_l or "opus" in mime_l


def convert_audio_to_ogg_opus(src_path: str) -> Optional[str]:
    """Return path to a temp .ogg file, or None if ffmpeg is unavailable/fails."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    fd, dest = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                src_path,
                "-c:a",
                "libopus",
                "-b:a",
                "64k",
                dest,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        if os.path.getsize(dest) > 0:
            return dest
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("ffmpeg audio→ogg failed: %s", e)
        try:
            os.unlink(dest)
        except OSError:
            pass
    return None


# Backwards-compatible alias
def convert_webm_to_ogg_opus(src_path: str) -> Optional[str]:
    return convert_audio_to_ogg_opus(src_path)


def prepare_bytes_for_meta(
    *,
    data: bytes,
    mime: str,
    filename: str,
    is_voice_note: bool,
) -> tuple[bytes, str, str, bool]:
    """
    Convert non-OGG voice notes to OGG/Opus via ffmpeg.
    Returns (bytes, mime, filename, is_voice_note_effective).
    """
    mime_l = (mime or "").split(";")[0].strip().lower()
    if not is_voice_note:
        return data, mime_l or mime, filename, False
    if _is_ogg_opus_mime(mime_l):
        return data, mime_l or "audio/ogg", filename, True

    suffix = _temp_suffix_for_audio_mime(mime_l, filename)
    fd, src = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, data)
        os.close(fd)
        fd = None
        ogg_path = convert_audio_to_ogg_opus(src)
        if not ogg_path:
            raise ValueError(_VOICE_CONVERT_ERROR)
        with open(ogg_path, "rb") as f:
            data = f.read()
        os.unlink(ogg_path)
        base = filename.rsplit(".", 1)[0] if "." in (filename or "") else (filename or "voice")
        return data, "audio/ogg", f"{base}.ogg", True
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(src)
        except OSError:
            pass


def upload_media_to_meta(
    *,
    phone_number_id: str,
    access_token: str,
    data: bytes,
    mime: str,
    filename: str,
) -> str:
    """Upload media to Meta; return media id."""
    url = f"{META_GRAPH_API_BASE_URL}/{phone_number_id}/media"
    files = {
        "file": (filename or "file", data, mime or "application/octet-stream"),
    }
    form = {"messaging_product": "whatsapp", "type": mime}
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.post(url, headers=headers, data=form, files=files, timeout=60)
    if resp.status_code >= 400:
        logger.warning("Meta media upload failed: status=%s body=%s", resp.status_code, resp.text[:500])
        raise ValueError("WhatsApp media upload failed.")
    body = resp.json() if resp.content else {}
    media_id = body.get("id")
    if not media_id:
        raise ValueError("WhatsApp media upload returned no id.")
    return str(media_id)


def download_media_from_meta(*, media_id: str, access_token: str) -> tuple[bytes, str]:
    """Download media bytes and mime type from Meta by media id."""
    headers = {"Authorization": f"Bearer {access_token}"}
    meta_url = f"{META_GRAPH_API_BASE_URL}/{media_id}"
    r1 = requests.get(meta_url, headers=headers, timeout=30)
    if r1.status_code >= 400:
        raise ValueError("Could not resolve WhatsApp media.")
    info = r1.json() if r1.content else {}
    download_url = info.get("url")
    mime = (info.get("mime_type") or "application/octet-stream").split(";")[0].strip()
    if not download_url:
        raise ValueError("WhatsApp media URL missing.")
    r2 = requests.get(download_url, headers=headers, timeout=60)
    if r2.status_code >= 400:
        raise ValueError("Could not download WhatsApp media.")
    return r2.content, mime


def graph_media_payload(
    *,
    kind: str,
    media_id: str,
    caption: str = "",
    filename: str = "",
    is_voice_note: bool = False,
) -> dict:
    """Build the typed object for Graph /messages payload."""
    if kind == KIND_IMAGE:
        obj: dict = {"id": media_id}
        if caption:
            obj["caption"] = caption[:1024]
        return {"type": "image", "image": obj}
    if kind == KIND_VIDEO:
        obj = {"id": media_id}
        if caption:
            obj["caption"] = caption[:1024]
        return {"type": "video", "video": obj}
    if kind == KIND_AUDIO:
        obj = {"id": media_id}
        if is_voice_note:
            obj["voice"] = True
        return {"type": "audio", "audio": obj}
    obj = {"id": media_id}
    if filename:
        obj["filename"] = filename[:240]
    if caption:
        obj["caption"] = caption[:1024]
    return {"type": "document", "document": obj}


def save_bytes_to_message_attachment(msg, data: bytes, filename: str, mime: str) -> None:
    """Persist bytes onto LeadWhatsAppMessage.attachment FileField."""
    safe_name = (filename or "attachment").replace("/", "_")[:200]
    msg.attachment.save(safe_name, ContentFile(data), save=False)
    msg.attachment_mime = mime or ""
    msg.attachment_size = len(data)
    msg.original_filename = safe_name


_MEDIA_TYPES = frozenset({"image", "video", "audio", "document", "sticker"})


def extract_meta_media_info(message: dict) -> tuple[str, dict] | None:
    """Return (kind, media_object) for Cloud API message payloads, or None."""
    if not isinstance(message, dict):
        return None
    msg_type = (message.get("type") or "").strip()
    if msg_type not in _MEDIA_TYPES:
        return None
    media = message.get(msg_type) or {}
    if not isinstance(media, dict):
        return None
    kind = KIND_IMAGE if msg_type == "sticker" else msg_type
    return kind, media


def apply_meta_media_to_message(msg, message: dict, *, access_token: str) -> bool:
    """
    Download Meta media onto LeadWhatsAppMessage when the webhook payload has a media id.
    Returns True if an attachment was stored. Does not call msg.save() for non-FileField fields
    beyond attachment.save(save=False); caller should save remaining fields.
    """
    info = extract_meta_media_info(message)
    if not info:
        return False
    kind, media = info
    media_id = media.get("id")
    if not media_id:
        return False
    try:
        data, mime = download_media_from_meta(media_id=str(media_id), access_token=access_token)
    except Exception as e:
        logger.warning("WhatsApp inbound media download failed media_id=%s: %s", media_id, e)
        return False

    mime = (media.get("mime_type") or mime or "application/octet-stream").split(";")[0].strip()
    filename = (media.get("filename") or "").strip()
    if not filename:
        ext = {
            KIND_IMAGE: ".jpg" if "jpeg" in mime else ".png" if "png" in mime else ".webp",
            KIND_VIDEO: ".mp4",
            KIND_AUDIO: ".ogg" if "ogg" in mime else ".mp3",
            KIND_DOCUMENT: ".bin",
        }.get(kind, ".bin")
        if mime == "application/pdf":
            ext = ".pdf"
        filename = f"{kind}-{media_id}{ext}"

    msg.attachment_kind = kind
    msg.meta_media_id = str(media_id)
    if kind == KIND_AUDIO and media.get("voice") is True:
        msg.is_voice_note = True
    save_bytes_to_message_attachment(msg, data, filename, mime)
    return True


_PLACEHOLDER_BODY_RE = (
    "[media message]",
    "[image message]",
    "[video message]",
    "[audio message]",
    "[document message]",
    "[sticker message]",
)


def caption_or_empty_from_meta_message(message: dict) -> str:
    """Caption text for media, or empty (avoid keeping '[image message]' under rendered media)."""
    info = extract_meta_media_info(message)
    if not info:
        return ""
    _kind, media = info
    return (media.get("caption") or "").strip()


def hydrate_existing_message_media(
    msg,
    message: dict,
    *,
    access_token: str,
) -> bool:
    """
    Attach Meta media to an already-saved LeadWhatsAppMessage (history media follow-up).
    Updates body when it was a placeholder. Saves the row. Returns True on success.
    """
    if getattr(msg, "attachment_kind", None) and getattr(msg, "attachment", None):
        try:
            if msg.attachment and msg.attachment.name:
                return True
        except Exception:
            pass
    if not apply_meta_media_to_message(msg, message, access_token=access_token):
        return False
    caption = caption_or_empty_from_meta_message(message)
    body = (msg.body or "").strip()
    if not body or body.lower() in _PLACEHOLDER_BODY_RE or body.startswith("["):
        msg.body = caption
    msg.save()
    return True


def media_body_from_meta_message(message: dict) -> str:
    """Caption or placeholder for media / other non-text types."""
    from integrations.services.whatsapp_coexistence import extract_whatsapp_message_body

    return extract_whatsapp_message_body(message) or ""
