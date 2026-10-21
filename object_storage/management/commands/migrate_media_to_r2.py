"""
Copy local media into R2.

    manage.py migrate_media_to_r2                 # dry run: what would be copied
    manage.py migrate_media_to_r2 --apply         # copy

Every file keeps its key, so database rows need no change. A file's target is the
storage its model field uses:

* ``MEDIA_ROOT/pbx_recordings/<key>``  → ``recordings`` at ``<key>``
* names stored in a field with its own storage (guide/news covers → ``public``,
  backups → ``backups``) → that storage; unreferenced files under
  ``platform_content/`` and ``backups/`` go to the same two
* everything else (attachments, library, photos, logos, avatars) → ``default``

Skipped: ``fonts/`` and the ``backups/pre-restore-*`` snapshots (local working
files), dotfiles and temporary folders.

Idempotent: an object that already exists with the same size is not copied again.
Logs bucket, key and operation only; never credentials.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db.models import FileField

from object_storage.backends import backup_storage, is_remote, public_storage, recordings_storage

logger = logging.getLogger(__name__)

SKIP_PREFIXES = ("fonts/", "backups/pre-restore-")
RECORDINGS_PREFIX = "pbx_recordings/"


def _bucket(storage) -> str:
    return getattr(storage, "bucket_name", "") or "local"


class Command(BaseCommand):
    help = "Copy MEDIA_ROOT files to R2 under the same keys. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Copy for real (default: dry run).")
        parser.add_argument("--source", default=None, help="Local media root to copy from (default: MEDIA_ROOT).")

    def handle(self, *args, **opts):
        self.apply = opts["apply"]
        self.counts: Counter = Counter()
        self.bytes_copied = 0
        mode = "APPLY" if self.apply else "DRY RUN"
        self.stdout.write(f"migrate_media_to_r2 ({mode})")

        source = Path(opts["source"] or settings.MEDIA_ROOT)
        if not source.is_dir():
            raise CommandError(f"Source media root does not exist: {source}")
        self._copy_media(source)

        c = self.counts
        self.stdout.write(
            "scanned={scanned} copied={copied} would_copy={would_copy} present={present} "
            "skipped={skipped} failed={failed} rows_repointed={rows} bytes={bytes}".format(
                scanned=c["scanned"], copied=c["copied"], would_copy=c["would_copy"], present=c["present"],
                skipped=c["skipped"], failed=c["failed"], rows=c["rows_repointed"], bytes=self.bytes_copied,
            )
        )
        for key in sorted(k for k in c if k.startswith("target:")):
            self.stdout.write(f"  {key[7:]}: {c[key]}")
        if c["failed"]:
            raise CommandError(f"{c['failed']} object(s) failed; re-run to retry (copies are idempotent).")

    # ------------------------------------------------------------------ targets

    def _field_storages(self) -> dict[str, object]:
        """name → storage, for every stored name in a field whose storage is not the default."""
        mapping: dict[str, object] = {}
        for model in apps.get_models():
            for field in model._meta.get_fields():
                if not isinstance(field, FileField) or field.storage is default_storage:
                    continue
                names = model._default_manager.exclude(**{field.name: ""}).exclude(**{f"{field.name}__isnull": True})
                for name in names.values_list(field.name, flat=True).iterator():
                    mapping[name] = field.storage
        return mapping

    def _require_remote(self, storage, label: str):
        if not is_remote(storage):
            raise CommandError(
                f"The '{label}' storage is local disk. Configure R2 (docs/STORAGE.md) before migrating."
            )

    # -------------------------------------------------------------------- media

    def _copy_media(self, root: Path):
        field_storages = self._field_storages()
        labels = {id(public_storage()): "public", id(backup_storage()): "backups", id(recordings_storage()): "recordings"}
        checked: set[int] = set()
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(SKIP_PREFIXES) or any(part.startswith(".") or part.startswith("crm-") for part in rel.split("/")):
                self.counts["skipped"] += 1
                continue
            key = rel
            if rel.startswith(RECORDINGS_PREFIX):
                storage, key = recordings_storage(), rel[len(RECORDINGS_PREFIX):]
            elif rel in field_storages:
                storage = field_storages[rel]
            elif rel.startswith("backups/"):
                storage = backup_storage()
            elif rel.startswith("platform_content/"):
                storage = public_storage()
            else:
                storage = default_storage
            label = labels.get(id(storage), "default")
            if id(storage) not in checked:
                self._require_remote(storage, label)
                checked.add(id(storage))
            self.counts["scanned"] += 1
            self._copy_one(storage, key, path.stat().st_size, lambda p=path: File(p.open("rb"), name=p.name))

    def _copy_one(self, storage, key: str, size: int, opener) -> bool:
        bucket = _bucket(storage)
        self.counts[f"target:{bucket}"] += 1
        try:
            if storage.exists(key):
                if storage.size(key) == size:
                    self.counts["present"] += 1
                    return True
                if self.apply:
                    storage.delete(key)  # a partial/different copy: replace it under the same key
            if not self.apply:
                self.counts["would_copy"] += 1
                self.bytes_copied += size
                return True
            content = opener()
            try:
                saved = storage.save(key, content)
            finally:
                content.close()
            if saved != key:
                raise RuntimeError(f"stored as {saved!r} instead of {key!r}")
            self.counts["copied"] += 1
            self.bytes_copied += size
            logger.info("object_storage.migrate op=put bucket=%s key=%s bytes=%s", bucket, key, size)
            return True
        except Exception as exc:
            self.counts["failed"] += 1
            self.stderr.write(f"FAILED {bucket}/{key}: {type(exc).__name__}: {exc}")
            logger.warning("object_storage.migrate op=put_failed bucket=%s key=%s error=%s", bucket, key, type(exc).__name__)
            return False
