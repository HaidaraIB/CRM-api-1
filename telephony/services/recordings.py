"""
Call recordings (CRM-pbx contract §7).

The PBX side uploads audio straight to object storage under
``telephony/<company_id>/<yyyy>/<mm>/<call_id>/<recording_id>.<ext>`` and reports
the metadata here. Django never downloads or proxies the audio in production:
playback is a presigned GET valid for ``ACCESS_TTL_SECONDS``, issued per request
after the caller's visibility check, and every issue is logged for audit.

With local recordings storage (development, ``settings.STORAGES["recordings"]``
on disk) there is no object store to presign against, so the URL is a signed, expiring link to a small streaming
view under /api/public/ — audio elements cannot send JWT headers.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta

from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone

from .. import contract
from ..models import PbxCall, PbxNode, PbxRecording, PbxTenant
from .events import BatchResult, InvalidEvent, _ts, _uuid

logger = logging.getLogger(__name__)

ACCESS_TTL_SECONDS = 300
MAX_BATCH = 100
_KEY_RE = re.compile(
    r"^telephony/(?P<company>[1-9][0-9]*)/(?P<yyyy>[0-9]{4})/(?P<mm>0[1-9]|1[0-2])/"
    r"(?P<call>[0-9a-f-]{36})/(?P<rec>[0-9a-f-]{36})\.(?P<ext>wav|ogg|mp3)$"
)
_SIGNING_SALT = "telephony.recording.stream"


def ingest_recordings(node: PbxNode, payload: dict) -> BatchResult:
    result = BatchResult()
    items = payload.get("recordings") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not (1 <= len(items) <= MAX_BATCH):
        raise InvalidEvent(f"recordings must be a list of 1-{MAX_BATCH}")
    touched: set[int] = set()

    for raw in items:
        raw_id = raw.get("recording_id") if isinstance(raw, dict) else None
        try:
            rec_id = _uuid(raw_id, "recording_id")
            call_id = _uuid(raw.get("call_id"), "call_id")
            tag = raw.get("tenant")
            if not isinstance(tag, str) or not contract.TENANT_TAG_RE.match(tag):
                raise InvalidEvent("invalid tenant tag")
            size, duration = raw.get("bytes"), raw.get("duration_s")
            if not isinstance(size, int) or isinstance(size, bool) or size < 1:
                raise InvalidEvent("bytes must be a positive integer")
            if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
                raise InvalidEvent("duration_s must be a non-negative integer")
            sha = raw.get("sha256")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise InvalidEvent("sha256 must be 64 hex chars")
            fmt = raw.get("format")
            started_at = _ts(raw.get("started_at"), "started_at")
        except InvalidEvent as exc:
            if raw_id:
                result.rejected.append({"id": str(raw_id), "code": "invalid_payload", "detail": str(exc)[:500]})
            continue

        tenant = PbxTenant.objects.filter(realm_key=contract.realm_key_from_tag(tag)).first()
        if tenant is None:
            result.rejected.append({"id": rec_id, "code": "unknown_tenant"})
            continue
        if node.id not in (tenant.home_node_id, tenant.draining_node_id):
            result.rejected.append({"id": rec_id, "code": "tenant_not_on_node"})
            continue
        m = _KEY_RE.match(str(raw.get("storage_key") or ""))
        if (
            m is None
            or int(m["company"]) != tenant.company_id
            or m["call"] != call_id
            or m["rec"] != rec_id
            or m["ext"] != fmt
        ):
            # Never trust a node to name another tenant's object.
            result.rejected.append({"id": rec_id, "code": "storage_key_outside_tenant_prefix"})
            continue

        call = PbxCall.objects.filter(company_id=tenant.company_id, pk=call_id).first()
        try:
            with transaction.atomic():
                PbxRecording.objects.create(
                    id=rec_id,
                    company_id=tenant.company_id,
                    tenant=tenant,
                    call=call,
                    call_uuid=call_id,
                    storage_key=m.group(0),
                    size_bytes=size,
                    sha256=sha,
                    duration_s=duration,
                    format=fmt,
                    started_at=started_at,
                )
        except IntegrityError:
            if PbxRecording.objects.filter(pk=rec_id).exists():
                result.duplicates.append(rec_id)
            else:
                result.rejected.append({"id": rec_id, "code": "invalid_payload", "detail": "conflict"})
            continue
        if call is not None:
            from crm.models import ClientCall

            ClientCall.objects.filter(pbx_call=call).exclude(recording_status="ready").update(recording_status="ready")
        result.accepted.append(rec_id)
        touched.add(tenant.company_id)

    if touched:
        from sync.version import bump_company_slice

        for company_id in touched:
            bump_company_slice("telephony", company_id)
        for tenant in PbxTenant.objects.filter(company_id__in=touched).select_related("company"):
            try:
                enforce_quota(tenant)
            except Exception:  # the recordings are stored; the quota check retries on the next batch
                logger.exception("telephony: recording quota check failed for company %s", tenant.company_id)
    return result


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------


_CONTENT_TYPES = {"wav": "audio/wav", "ogg": "audio/ogg", "mp3": "audio/mpeg"}


def _storage():
    from integrations.storage.recordings import storage

    return storage()


def is_object_storage() -> bool:
    from object_storage.backends import is_remote

    return is_remote(_storage())


def content_type(recording: PbxRecording) -> str:
    return _CONTENT_TYPES.get(recording.format, "application/octet-stream")


def access_url(recording: PbxRecording, *, user, build_absolute_uri) -> dict:
    """Short-lived playback URL for one recording; the caller already checked visibility."""
    expires_at = timezone.now() + timedelta(seconds=ACCESS_TTL_SECONDS)
    if is_object_storage():
        url = _storage().url(
            recording.storage_key,
            parameters={"ResponseContentType": content_type(recording)},
            expire=ACCESS_TTL_SECONDS,
        )
    else:
        token = signing.dumps({"r": str(recording.pk), "u": user.pk}, salt=_SIGNING_SALT, compress=True)
        url = build_absolute_uri(f"/api/v1/public/telephony/recordings/{token}/")
    logger.info(
        "telephony.recording_access user=%s company=%s recording=%s call=%s",
        user.pk, recording.company_id, recording.pk, recording.call_id,
    )
    return {"url": url, "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")}


def resolve_stream_token(token: str) -> PbxRecording | None:
    try:
        data = signing.loads(token, salt=_SIGNING_SALT, max_age=ACCESS_TTL_SECONDS)
        return PbxRecording.objects.filter(pk=uuid.UUID(data["r"])).first()
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Storage quota, deletion and retention (Phase 5)
# ---------------------------------------------------------------------------


def storage_used(company_id) -> int:
    from django.db.models import Sum

    return int(PbxRecording.objects.filter(company_id=company_id).aggregate(n=Sum("size_bytes"))["n"] or 0)


def storage_limit(company) -> int | None:
    """``max_pbx_recording_bytes`` of the company's plan; None = unlimited."""
    from subscriptions.entitlements import build_company_entitlements, resolve_quota_limit

    return resolve_quota_limit(build_company_entitlements(company), "max_pbx_recording_bytes")


def enforce_quota(tenant: PbxTenant) -> bool:
    """
    Pause new recordings while stored ones fill the plan's ``max_pbx_recording_bytes``
    and resume once space is freed (deletion, retention, a bigger plan). The flag
    reaches the node through a recompile: the dialplan stops starting MixMonitor.
    Calls already recording finish, so usage can end slightly above the limit.
    Returns whether the flag changed.
    """
    from settings.services import log_system_action

    from . import provisioning

    limit = storage_limit(tenant.company)
    used = storage_used(tenant.company_id)
    full = limit is not None and used >= limit
    with transaction.atomic():
        changed = PbxTenant.objects.filter(pk=tenant.pk).exclude(recording_paused_quota=full).update(
            recording_paused_quota=full
        )
        if changed:
            tenant.recording_paused_quota = full
            provisioning.request_recompile_for_company(tenant.company)
            log_system_action(
                "telephony.recording_quota_paused" if full else "telephony.recording_quota_resumed",
                message=f"Call recording {'paused: storage full' if full else 'resumed'} ({used} of {limit} bytes)",
                metadata={"company_id": tenant.company_id, "used_bytes": used, "limit_bytes": limit},
            )
    return bool(changed)


class StorageError(RuntimeError):
    pass


def delete_object(recording: PbxRecording) -> None:
    """Remove the audio from the recordings storage. Missing is fine (idempotent in S3/R2 and on disk)."""
    try:
        _storage().delete(recording.storage_key)
    except Exception as exc:  # network / credentials: keep the row, retry on the next run
        logger.warning("telephony.recording_delete_failed key=%s error=%s", recording.storage_key, type(exc).__name__)
        raise StorageError(str(exc)) from exc


def delete_recording(recording: PbxRecording, *, user=None, reason: str) -> None:
    """Object first, then the row: a failed delete never leaves audio nobody can find."""
    from crm.models import ClientCall
    from settings.services import log_system_action

    delete_object(recording)
    rec_id, call_id, company_id = recording.pk, recording.call_id, recording.company_id
    recording.delete()
    if call_id and not PbxRecording.objects.filter(call_id=call_id).exists():
        ClientCall.objects.filter(pbx_call_id=call_id).update(recording_status="deleted")
    if user is not None:
        log_system_action(
            "telephony.recording_deleted", user=user, message=f"Call recording deleted ({reason})",
            metadata={"company_id": company_id, "recording_id": str(rec_id), "call_id": str(call_id), "reason": reason},
        )


def purge_expired(*, now=None, dry_run: bool = False, batch: int = 500) -> dict:
    """
    Retention job (``manage.py purge_pbx_recordings``, daily): delete recordings
    older than each tenant's ``recording_retention_days``. Tenants on legal hold are
    skipped. Returns {company_id: {"deleted": n, "bytes": b, "failed": f}}.
    """
    from settings.services import log_system_action

    now = now or timezone.now()
    report = {}
    for tenant in PbxTenant.objects.filter(recording_legal_hold=False).select_related("company"):
        cutoff = now - timedelta(days=max(1, tenant.recording_retention_days))
        expired = list(PbxRecording.objects.filter(company_id=tenant.company_id, started_at__lt=cutoff)
                       .order_by("started_at")[:batch])
        if not expired:
            continue
        stats = {"deleted": 0, "bytes": 0, "failed": 0}
        for rec in expired:
            if dry_run:
                stats["deleted"] += 1
                stats["bytes"] += rec.size_bytes
                continue
            try:
                delete_recording(rec, reason="retention")
            except StorageError as exc:
                stats["failed"] += 1
                logger.warning("telephony: retention could not delete %s: %s", rec.pk, exc)
                continue
            stats["deleted"] += 1
            stats["bytes"] += rec.size_bytes
        report[tenant.company_id] = stats
        if not dry_run and stats["deleted"]:
            log_system_action(
                "telephony.recordings_purged",
                message=f"Retention deleted {stats['deleted']} call recording(s) older than {tenant.recording_retention_days} days",
                metadata={"company_id": tenant.company_id, **stats, "retention_days": tenant.recording_retention_days},
            )
            enforce_quota(tenant)
    return report
