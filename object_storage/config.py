"""
One object-storage configuration for the whole API (docs/STORAGE.md).

``settings.py`` calls :func:`storages_from_env` once; everything else reads the
resulting ``STORAGES`` through ``django.core.files.storage.storages``. This module
imports nothing from Django apps so settings can use it.

Aliases:

* ``default``    private, tenant-owned media (attachments, library, photos, logos)
* ``public``     platform content images (guide / news covers); a public bucket
                 behind a custom domain, or the private bucket when none is set
* ``backups``    database backups (private)
* ``recordings`` call recordings: PBX (uploaded by the node) and WhatsApp calls
* ``staticfiles`` unchanged

With nothing set, every alias is local disk under ``MEDIA_ROOT``, as before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

R2_BACKEND = "object_storage.backends.R2Storage"
LOCAL_BACKEND = "django.core.files.storage.FileSystemStorage"
MEDIA_SUBDIR_BACKEND = "object_storage.backends.MediaSubdirStorage"
STATIC_BACKEND = "django.contrib.staticfiles.storage.StaticFilesStorage"

DEFAULT_MEDIA_BUCKET = "loop-crm-media"
DEFAULT_RECORDINGS_BUCKET = "loop-pbx-recordings"
# Presigned media URLs: valid for at least TTL, identical inside one WINDOW
# (object_storage/presign.py). Six hours outlives how long any client keeps a
# list response; one hour keeps browser and image caches warm.
DEFAULT_MEDIA_URL_TTL = 6 * 3600
DEFAULT_URL_WINDOW = 3600
RECORDING_URL_TTL = 300  # contract §7: playback URLs live ≤ 5 min

_REMOTE = {"r2", "s3"}


@dataclass(frozen=True)
class R2Settings:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    region: str = "auto"
    media_bucket: str = DEFAULT_MEDIA_BUCKET
    public_bucket: str = ""
    public_domain: str = ""
    backup_bucket: str = ""
    recordings_bucket: str = DEFAULT_RECORDINGS_BUCKET
    media_url_ttl: int = DEFAULT_MEDIA_URL_TTL
    url_window: int = DEFAULT_URL_WINDOW

    def __repr__(self) -> str:  # never print credentials
        return (
            f"R2Settings(endpoint_url={self.endpoint_url!r}, region={self.region!r}, "
            f"media_bucket={self.media_bucket!r}, public_bucket={self.public_bucket!r}, "
            f"backup_bucket={self.backup_bucket or self.media_bucket!r}, "
            f"recordings_bucket={self.recordings_bucket!r})"
        )


def _env(env, *names: str, default: str = "") -> str:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return default


def _secret(env) -> str:
    value = _env(env, "R2_SECRET_ACCESS_KEY")
    path = _env(env, "R2_SECRET_ACCESS_KEY_FILE")
    if not value and path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ImproperlyConfigured(f"R2_SECRET_ACCESS_KEY_FILE is not readable: {path}") from exc
    # Deprecated: the recordings client used boto3's default chain, which read these.
    return value or _env(env, "AWS_SECRET_ACCESS_KEY")


def _int(env, name: str, default: int, low: int, high: int) -> int:
    raw = _env(env, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer") from exc
    if not low <= value <= high:
        raise ImproperlyConfigured(f"{name} must be between {low} and {high}")
    return value


def r2_settings_from_env(env=None) -> R2Settings | None:
    """The R2 connection, or None when no endpoint is configured anywhere."""
    env = os.environ if env is None else env
    account = _env(env, "R2_ACCOUNT_ID")
    endpoint = _env(env, "R2_ENDPOINT_URL", "RECORDING_S3_ENDPOINT_URL")
    if not endpoint and account:
        endpoint = f"https://{account}.r2.cloudflarestorage.com"
    if not endpoint:
        return None
    return R2Settings(
        endpoint_url=endpoint.rstrip("/"),
        access_key_id=_env(env, "R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
        secret_access_key=_secret(env),
        region=_env(env, "R2_REGION", "RECORDING_S3_REGION", default="auto"),
        media_bucket=_env(env, "R2_MEDIA_BUCKET", default=DEFAULT_MEDIA_BUCKET),
        public_bucket=_env(env, "R2_PUBLIC_BUCKET"),
        public_domain=_env(env, "R2_PUBLIC_DOMAIN").removeprefix("https://").rstrip("/"),
        backup_bucket=_env(env, "R2_BACKUP_BUCKET"),
        recordings_bucket=_env(
            env, "R2_RECORDINGS_BUCKET", "TELEPHONY_RECORDING_BUCKET", "RECORDING_S3_BUCKET",
            default=DEFAULT_RECORDINGS_BUCKET,
        ),
        media_url_ttl=_int(env, "R2_MEDIA_URL_TTL", DEFAULT_MEDIA_URL_TTL, 300, 6 * 24 * 3600),
        url_window=_int(env, "R2_URL_WINDOW", DEFAULT_URL_WINDOW, 0, 24 * 3600),
    )


def _backend_choice(raw: str, name: str) -> str:
    value = (raw or "").strip().lower()
    if value in _REMOTE:
        return "r2"
    if value == "local":
        return "local"
    raise ImproperlyConfigured(f"{name} must be 'local' or 'r2' (got {raw!r})")


def backends_from_env(env=None) -> tuple[str, str]:
    """(media backend, recordings backend), each 'local' or 'r2'."""
    env = os.environ if env is None else env
    media_raw = _env(env, "OBJECT_STORAGE_BACKEND")
    if not media_raw:
        media_raw = "r2" if _env(env, "R2_ENDPOINT_URL", "R2_ACCOUNT_ID") else "local"
    media = _backend_choice(media_raw, "OBJECT_STORAGE_BACKEND")
    recordings = _backend_choice(_env(env, "RECORDING_STORAGE_BACKEND", default=media), "RECORDING_STORAGE_BACKEND")
    return media, recordings


def _r2(r2: R2Settings, bucket: str, **options) -> dict:
    return {
        "BACKEND": R2_BACKEND,
        "OPTIONS": {
            "endpoint_url": r2.endpoint_url,
            "region_name": r2.region,
            "access_key": r2.access_key_id,
            "secret_key": r2.secret_access_key,
            "bucket_name": bucket,
            **options,
        },
    }


def build_storages(*, media: str, recordings: str, r2: R2Settings | None) -> dict:
    """The ``STORAGES`` setting for one configuration. Pure: tests call it too."""
    if "r2" in (media, recordings):
        if r2 is None:
            raise ImproperlyConfigured("R2 storage needs R2_ENDPOINT_URL or R2_ACCOUNT_ID")
        if not (r2.access_key_id and r2.secret_access_key):
            raise ImproperlyConfigured("R2 storage needs R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY")
        if r2.public_bucket and not r2.public_domain:
            raise ImproperlyConfigured("R2_PUBLIC_BUCKET needs R2_PUBLIC_DOMAIN (its custom domain or r2.dev host)")

    # No fixed location: local storages follow MEDIA_ROOT (and test overrides of it).
    local = {"BACKEND": LOCAL_BACKEND}
    result: dict = {"staticfiles": {"BACKEND": STATIC_BACKEND}}

    if media == "r2":
        private = {
            "file_overwrite": False,  # FileSystemStorage renames on collision; so do we
            "querystring_expire": r2.media_url_ttl,
            "url_window": r2.url_window,
            "object_parameters": {"CacheControl": "private, max-age=86400"},
        }
        result["default"] = _r2(r2, r2.media_bucket, **private)
        result["backups"] = _r2(r2, r2.backup_bucket or r2.media_bucket, file_overwrite=False,
                                querystring_expire=RECORDING_URL_TTL)
        if r2.public_bucket:
            result["public"] = _r2(
                r2, r2.public_bucket, file_overwrite=False, querystring_auth=False,
                custom_domain=r2.public_domain, object_parameters={"CacheControl": "public, max-age=86400"},
            )
        else:
            result["public"] = _r2(r2, r2.media_bucket, **private)
    else:
        result["default"] = local
        result["public"] = local
        result["backups"] = local

    if recordings == "r2":
        # Keys are unique (uuid / deterministic recording id), so skip the HEAD
        # per save; playback URLs are exact and short (window 0).
        result["recordings"] = _r2(r2, r2.recordings_bucket, file_overwrite=True,
                                   querystring_expire=RECORDING_URL_TTL, url_window=0)
    else:
        result["recordings"] = {"BACKEND": MEDIA_SUBDIR_BACKEND, "OPTIONS": {"subdir": "pbx_recordings"}}
    return result


def storages_from_env(env=None) -> tuple[dict, str, str]:
    """(STORAGES, media backend, recordings backend) for settings.py."""
    media, recordings = backends_from_env(env)
    r2 = r2_settings_from_env(env) if "r2" in (media, recordings) else None
    return build_storages(media=media, recordings=recordings, r2=r2), media, recordings
