"""PBX call recording object storage (local dev, S3/R2 prod)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def _s3_client():
    """Lazy boto3 import — only required when RECORDING_STORAGE_BACKEND is s3 or r2."""
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for S3/R2 recording storage. Install with: pip install boto3"
        ) from exc

    client_kwargs: dict = {}
    endpoint = (getattr(settings, "RECORDING_S3_ENDPOINT_URL", "") or "").strip()
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
    region = (getattr(settings, "RECORDING_S3_REGION", "") or "").strip()
    if region:
        client_kwargs["region_name"] = region
    return boto3.client("s3", **client_kwargs)


def _backend_name() -> str:
    return (getattr(settings, "RECORDING_STORAGE_BACKEND", "local") or "local").strip().lower()


def build_storage_key(company_id: int, linkedid: str, original_name: str) -> str:
    ext = Path(original_name).suffix.lower() or ".wav"
    safe_linked = "".join(c if c.isalnum() or c in ".-_" else "_" for c in linkedid)[:64]
    return f"pbx/{company_id}/{safe_linked}/{uuid.uuid4().hex}{ext}"


def save_recording(
    *,
    company_id: int,
    linkedid: str,
    file_bytes: bytes,
    original_filename: str,
) -> str:
    """Persist recording bytes; returns opaque storage key."""
    key = build_storage_key(company_id, linkedid, original_filename)
    backend = _backend_name()

    if backend in ("s3", "r2"):
        return _save_s3(key, file_bytes)

    return _save_local(key, file_bytes)


def _save_local(key: str, file_bytes: bytes) -> str:
    root = Path(settings.MEDIA_ROOT) / "pbx_recordings"
    dest = root / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(file_bytes)
    logger.info("Saved PBX recording locally key=%s bytes=%s", key, len(file_bytes))
    return key


def _save_s3(key: str, file_bytes: bytes) -> str:
    bucket = (getattr(settings, "RECORDING_S3_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("RECORDING_S3_BUCKET is not configured")

    client = _s3_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=file_bytes,
        ContentType=_guess_content_type(key),
    )
    logger.info("Saved PBX recording to S3 key=%s bytes=%s", key, len(file_bytes))
    return key


def open_recording(storage_key: str):
    """Return a readable binary file-like object for streaming."""
    if not storage_key:
        raise FileNotFoundError("empty storage key")
    backend = _backend_name()
    if backend in ("s3", "r2"):
        return _open_s3(storage_key)
    return _open_local(storage_key)


def _open_local(storage_key: str):
    path = Path(settings.MEDIA_ROOT) / "pbx_recordings" / storage_key
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.open("rb")


def _open_s3(storage_key: str):
    bucket = (getattr(settings, "RECORDING_S3_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("RECORDING_S3_BUCKET is not configured")
    client = _s3_client()
    return client.get_object(Bucket=bucket, Key=storage_key)["Body"]


def _guess_content_type(key: str) -> str:
    ext = Path(key).suffix.lower()
    if ext == ".wav":
        return "audio/wav"
    if ext == ".gsm":
        return "audio/x-gsm"
    if ext == ".mp3":
        return "audio/mpeg"
    return "application/octet-stream"
