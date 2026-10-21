"""
Smoke-test the configured object storage (docs/STORAGE.md, operator setup).

    manage.py check_object_storage

For each alias on R2 (default, public, backups, recordings): upload a small
object, read it back through a presigned GET (or the public domain), check the
response-content-disposition override, then delete it. Run it once after
setting the R2_* variables in production; it is how R2's compatibility
(checksum settings, path-style addressing, presigning) is verified for real,
since the test suite never talks to R2.

Prints bucket, key and result only; never credentials.
"""

from __future__ import annotations

import urllib.request
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django.utils.http import content_disposition_header

from object_storage.backends import is_remote

ALIASES = ("default", "public", "backups", "recordings")


class Command(BaseCommand):
    help = "Put / presigned GET / delete a probe object in every R2-backed storage alias."

    def add_arguments(self, parser):
        parser.add_argument("--alias", action="append", choices=ALIASES, help="Only these aliases (repeatable).")

    def handle(self, *args, **opts):
        failures = 0
        seen = set()
        for alias in opts["alias"] or ALIASES:
            storage = storages[alias]
            if not is_remote(storage):
                self.stdout.write(f"{alias}: local disk ({storage.location}); nothing to check")
                continue
            ident = (storage.bucket_name, bool(storage.custom_domain))
            if ident in seen:
                self.stdout.write(f"{alias}: same bucket as an alias already checked ({storage.bucket_name})")
                continue
            seen.add(ident)
            try:
                self._check(alias, storage)
            except Exception as exc:
                failures += 1
                self.stderr.write(f"{alias}: FAILED bucket={storage.bucket_name}: {type(exc).__name__}: {exc}")
        if failures:
            raise CommandError(f"{failures} storage check(s) failed")

    def _check(self, alias, storage):
        key = f"_healthcheck/{uuid.uuid4().hex}.txt"
        body = f"loop-crm storage check {key}".encode()
        content = ContentFile(body)
        content.content_type = "text/plain"
        saved = storage.save(key, content)
        try:
            if storage.custom_domain:
                url = storage.url(saved)
                expect_disposition = None
            else:
                expect_disposition = content_disposition_header(True, "check.txt")
                url = storage.url(saved, parameters={"ResponseContentDisposition": expect_disposition})
            with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (our own bucket URL)
                got = resp.read()
                disposition = resp.headers.get("Content-Disposition")
            if got != body:
                raise RuntimeError("read back different bytes")
            if expect_disposition and disposition != expect_disposition:
                raise RuntimeError(f"Content-Disposition override not applied (got {disposition!r})")
            with storage.open(saved, "rb") as fh:
                if fh.read(4) != body[:4]:
                    raise RuntimeError("a read through the S3 API returned different bytes")
        finally:
            storage.delete(saved)
        if storage.exists(saved):
            raise RuntimeError("probe object still exists after delete")
        self.stdout.write(f"{alias}: ok bucket={storage.bucket_name} key={saved}")
