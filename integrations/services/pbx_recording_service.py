"""PBX call recordings: connector polls pending jobs and uploads to CRM storage."""

from __future__ import annotations

import logging
from typing import Any

from django.core import signing
from django.db import transaction

from integrations.models import PbxCallRecord, PbxRecordingStatus, PbxSettings
from integrations.storage.recordings import open_recording, save_recording

logger = logging.getLogger(__name__)

RECORDING_JOB_LIMIT = 10
_PLAY_TOKEN_SALT = "pbx-recording-play"
_PLAY_TOKEN_MAX_AGE = 60 * 60 * 24  # 24 hours


def sign_playback_token(record_id: int, company_id: int) -> str:
    return signing.dumps(
        {"rid": record_id, "cid": company_id},
        salt=_PLAY_TOKEN_SALT,
    )


def verify_playback_token(token: str) -> tuple[int, int]:
    data = signing.loads(token, salt=_PLAY_TOKEN_SALT, max_age=_PLAY_TOKEN_MAX_AGE)
    return int(data["rid"]), int(data["cid"])


def build_recording_download_url(
    settings: PbxSettings, recording_path: str, recording_url: str = ""
) -> str:
    """Map ZYCOO CDR filesystem path to HTTP URL the LAN connector can fetch."""
    if recording_url:
        return recording_url
    if not recording_path:
        return ""
    path = recording_path.strip()
    if path.startswith("http://") or path.startswith("https://"):
        return path

    host = (settings.pbx_host or "").strip().rstrip("/")
    if not host:
        return ""

    if host.startswith("http://") or host.startswith("https://"):
        base = host.rstrip("/")
    else:
        scheme = "https" if ".zycoo.com" in host else "http"
        base = f"{scheme}://{host}"

    # ZYCOO CDR example:
    # /var/spool/asterisk/monitor/recording/20260603/104/<file>.wav
    relative = path
    for prefix in (
        "/var/spool/asterisk/monitor/recording/",
        "/var/spool/asterisk/monitor/",
        "/var/spool/asterisk/",
    ):
        if path.startswith(prefix):
            relative = path[len(prefix) :]
            break

    if relative and not relative.startswith("/"):
        return f"{base}/monitor/recording/{relative.lstrip('/')}"

    if path.startswith("/"):
        return f"{base}{path}"
    return ""


def apply_recording_path_from_cdr(
    record: PbxCallRecord,
    recording_path: str,
    *,
    settings: PbxSettings | None = None,
) -> bool:
    """
    Store PBX path and HTTP download URL; LAN connector fetches via recording-jobs poll.
    Returns True if the recording was newly queued.
    """
    path = (recording_path or "").strip()
    if not path or not path.endswith((".wav", ".WAV", ".gsm", ".GSM", ".mp3", ".MP3")):
        return False

    if record.recording_uploaded and record.recording_status == PbxRecordingStatus.READY:
        return False

    download_url = ""
    if settings is not None:
        download_url = build_recording_download_url(settings, path, record.recording_url)

    if (
        record.recording_path == path
        and record.recording_url == download_url
        and record.recording_status in (
            PbxRecordingStatus.PENDING,
            PbxRecordingStatus.PROCESSING,
            PbxRecordingStatus.READY,
        )
    ):
        return False

    if not download_url:
        logger.warning(
            "PBX recording skipped (no download URL) record_id=%s path=%s pbx_host=%r",
            record.id,
            path[:80],
            settings.pbx_host if settings else None,
        )
        return False

    record.recording_path = path
    record.recording_url = download_url
    record.recording_status = PbxRecordingStatus.PENDING
    record.recording_uploaded = False
    record.save(
        update_fields=[
            "recording_path",
            "recording_status",
            "recording_uploaded",
            "recording_url",
            "updated_at",
        ]
    )
    logger.info(
        "PBX recording queued for connector poll record_id=%s url=%s",
        record.id,
        download_url[:120],
    )
    return True


def list_pending_recording_jobs(company_id: int) -> list[dict[str, Any]]:
    qs = (
        PbxCallRecord.objects.filter(
            company_id=company_id,
            recording_status__in=(
                PbxRecordingStatus.PENDING,
                PbxRecordingStatus.PROCESSING,
            ),
        )
        .exclude(recording_url="")
        .order_by("updated_at")[:RECORDING_JOB_LIMIT]
    )
    jobs = []
    for rec in qs:
        download_url = (rec.recording_url or "").strip()
        if not download_url.startswith(("http://", "https://")):
            continue
        if rec.recording_status == PbxRecordingStatus.PENDING:
            rec.recording_status = PbxRecordingStatus.PROCESSING
            rec.save(update_fields=["recording_status", "updated_at"])
        jobs.append(
            {
                "record_id": rec.id,
                "linkedid": rec.linkedid or rec.uniqueid,
                "file": rec.recording_path,
                "download_url": download_url,
            }
        )
    return jobs


@transaction.atomic
def finalize_recording_upload(
    *,
    record_id: int,
    company_id: int,
    file_bytes: bytes | None,
    original_filename: str,
    storage_key: str = "",
    public_url: str = "",
) -> PbxCallRecord:
    record = PbxCallRecord.objects.select_for_update().get(
        pk=record_id, company_id=company_id
    )

    if file_bytes:
        linked = record.linkedid or record.uniqueid
        name = original_filename or record.recording_path.split("/")[-1] or "recording.wav"
        storage_key = save_recording(
            company_id=company_id,
            linkedid=linked,
            file_bytes=file_bytes,
            original_filename=name,
        )

    if not storage_key and not public_url:
        _set_status(record, PbxRecordingStatus.FAILED)
        return record

    record.recording_storage_key = storage_key or record.recording_storage_key
    record.recording_uploaded = True
    record.recording_status = PbxRecordingStatus.READY
    record.recording_url = public_url or ""
    record.save(
        update_fields=[
            "recording_storage_key",
            "recording_uploaded",
            "recording_status",
            "recording_url",
            "updated_at",
        ]
    )
    logger.info(
        "PBX recording ready record_id=%s company_id=%s key=%s",
        record.id,
        company_id,
        record.recording_storage_key,
    )
    return record


def mark_recording_failed(record_id: int, *, company_id: int | None = None) -> None:
    qs = PbxCallRecord.objects.filter(pk=record_id)
    if company_id is not None:
        qs = qs.filter(company_id=company_id)
    record = qs.first()
    if record:
        _set_status(record, PbxRecordingStatus.FAILED)


def _set_status(record: PbxCallRecord, status: str) -> None:
    record.recording_status = status
    record.save(update_fields=["recording_status", "updated_at"])


def get_playback_url(record: PbxCallRecord, request=None) -> str | None:
    """Time-limited signed playback URL (works in browser without JWT header)."""
    if record.recording_status != PbxRecordingStatus.READY:
        return None
    if record.recording_url:
        return record.recording_url
    if not record.recording_storage_key:
        return None
    token = sign_playback_token(record.id, record.company_id)
    path = f"/api/integrations/pbx/recordings/{record.id}/play/?token={token}"
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def stream_recording_for_user(record: PbxCallRecord):
    """Open storage blob for authenticated playback."""
    if record.recording_status != PbxRecordingStatus.READY:
        raise FileNotFoundError("recording not ready")
    if not record.recording_storage_key:
        raise FileNotFoundError("missing storage key")
    return open_recording(record.recording_storage_key)
