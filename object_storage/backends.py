"""
Cloudflare R2 through django-storages, plus lazy handles for non-default aliases.

R2 speaks the S3 API at ``https://<account>.r2.cloudflarestorage.com`` with region
``auto`` and SigV4. It has no object ACLs: private objects are read through
presigned URLs, public ones through a bucket's custom domain.
"""

from __future__ import annotations

import os

from botocore.config import Config
from django.conf import settings as dj_settings
from django.core.files.storage import FileSystemStorage, storages
from django.core.signals import setting_changed
from django.utils.functional import LazyObject, cached_property, empty
from storages.backends.s3 import S3Storage
from storages.utils import clean_name

from .presign import presign_get_url


def r2_client_config(**overrides) -> Config:
    """
    botocore settings R2 needs:

    * path-style addressing (``<endpoint>/<bucket>/<key>``), which R2 and the dev
      SeaweedFS both serve, so no per-bucket DNS is involved;
    * checksums only when an operation requires them. Since 1.36 boto3 adds CRC32
      checksum headers to every upload by default, and R2 has rejected those
      ("We encountered an internal error"); Cloudflare's guidance is
      ``when_required``.
    """
    options = {
        "signature_version": "s3v4",
        "s3": {"addressing_style": "path"},
        "request_checksum_calculation": "when_required",
        "response_checksum_validation": "when_required",
        "connect_timeout": 5,
        "read_timeout": 60,
        "retries": {"max_attempts": 3, "mode": "standard"},
    }
    options.update(overrides)
    return Config(**options)


class R2Storage(S3Storage):
    """
    ``S3Storage`` with explicit credentials only and stable presigned URLs.

    * Credentials come from OPTIONS. The ambient AWS chain (``AWS_*`` env, shared
      config files, instance metadata) is never consulted.
    * ``url()`` signs with :func:`presign.presign_get_url`: identical inside one
      ``url_window`` and valid for at least ``querystring_expire`` seconds.
    """

    def get_default_settings(self):
        defaults = super().get_default_settings()
        defaults.update(
            access_key=None,
            secret_key=None,
            security_token=None,
            session_profile=None,
            region_name="auto",
            signature_version="s3v4",
            addressing_style="path",
            client_config=None,
            url_window=0,
        )
        return defaults

    def __init__(self, **settings):
        super().__init__(**settings)
        if settings.get("client_config") is None:
            self.client_config = r2_client_config(proxies=self.proxies)

    def url(self, name, parameters=None, expire=None, http_method=None):
        if self.custom_domain or not self.querystring_auth or http_method not in (None, "GET"):
            return super().url(name, parameters=parameters, expire=expire, http_method=http_method)
        key = self._normalize_name(clean_name(name))
        return presign_get_url(
            endpoint_url=self.endpoint_url,
            bucket=self.bucket_name,
            key=key,
            region=self.region_name,
            access_key=self.access_key,
            secret_key=self.secret_key,
            expires=self.querystring_expire if expire is None else expire,
            window=self.url_window,
            parameters=parameters,
        )


class MediaSubdirStorage(FileSystemStorage):
    """Local disk under ``MEDIA_ROOT/<subdir>``, following MEDIA_ROOT like the default storage."""

    def __init__(self, subdir: str = "", **kwargs):
        self._subdir = subdir
        kwargs.setdefault("base_url", None)
        super().__init__(**kwargs)

    @cached_property
    def base_location(self):
        return self._value_or_setting(self._location, os.path.join(dj_settings.MEDIA_ROOT, self._subdir))


def is_remote(storage) -> bool:
    """True for object storage (presigned URLs); False for local disk."""
    return isinstance(storage, S3Storage)


class AliasStorage(LazyObject):
    """``storages[alias]``, resolved on first use and again after STORAGES changes."""

    def __init__(self, alias: str):
        super().__init__()
        self.__dict__["_alias"] = alias

    def _setup(self):
        self._wrapped = storages[self.__dict__["_alias"]]


_public = AliasStorage("public")
_backups = AliasStorage("backups")
_recordings = AliasStorage("recordings")


def public_storage():
    """``FileField(storage=public_storage)``: platform content images."""
    return _public


def backup_storage():
    """``FileField(storage=backup_storage)``: database backups."""
    return _backups


def recordings_storage():
    return _recordings


def _reset_aliases(*, setting, **kwargs):
    if setting in ("STORAGES", "MEDIA_ROOT", "MEDIA_URL"):
        for lazy in (_public, _backups, _recordings):
            lazy._wrapped = empty


setting_changed.connect(_reset_aliases)
