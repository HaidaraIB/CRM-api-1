from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from django.conf import settings as project_settings
from django.core.files import File
from django.db import connections
from django.utils import timezone

from .models import SystemBackup, SystemAuditLog

logger = logging.getLogger(__name__)


def get_backup_root() -> Path:
    backup_root = Path(getattr(project_settings, "BACKUP_ROOT", project_settings.MEDIA_ROOT / "backups"))
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root


def _clean_old_pre_restore_snapshots(keep_last: int = 2) -> None:
    """Remove old pre-restore snapshots, keeping only the most recent ones."""
    backup_root = get_backup_root()
    pre_restore = sorted(backup_root.glob("pre-restore-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in pre_restore[keep_last:]:
        try:
            path.unlink()
        except OSError:
            pass


def log_system_action(action: str, user=None, message: str = "", metadata: Optional[dict] = None, ip_address: Optional[str] = None) -> SystemAuditLog:
    return SystemAuditLog.objects.create(
        action=action,
        message=message,
        actor=user,
        metadata=metadata or {},
        ip_address=ip_address,
    )


def _ensure_sqlite_backend():
    db_config = project_settings.DATABASES["default"]
    engine = db_config["ENGINE"]
    if "sqlite" not in engine:
        raise NotImplementedError("Only SQLite backups are supported at this time.")
    return Path(db_config["NAME"]), engine


def _backup_storage():
    """The ``backups`` storage: the private R2 bucket in production, MEDIA_ROOT/backups locally."""
    return SystemBackup._meta.get_field("file").storage


def create_database_backup(initiator: str = SystemBackup.Initiator.MANUAL, user=None, notes: str = "") -> SystemBackup:
    """
    Copy the database into the ``backups`` storage under ``backups/<name>``.

    The copy is staged in a temporary directory and uploaded through the storage
    API, so it works the same on local disk and on R2.
    """
    db_path, engine = _ensure_sqlite_backend()
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found at {db_path}")

    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    filename = f"{db_path.stem}-{timestamp}.sqlite3"

    backup = SystemBackup.objects.create(
        initiator=initiator,
        created_by=user,
        notes=notes,
        metadata={"engine": engine, "source": str(db_path)},
    )

    storage = _backup_storage()
    saved_name = None
    try:
        with tempfile.TemporaryDirectory(prefix="crm-backup-") as tmp:
            staged = Path(tmp) / filename
            shutil.copy2(db_path, staged)
            key = storage.get_available_name(f"backups/{filename}")
            backup.file.name = key
            backup.file_size = staged.stat().st_size
            backup.save(update_fields=["file", "file_size"])
            backup.mark_completed(file_size=backup.file_size, metadata={"filename": key})
            # Copy again so the stored file contains this completed record (with its
            # file name). Otherwise a restore would load a snapshot where the record
            # was still in progress with no file.
            shutil.copy2(db_path, staged)
            with staged.open("rb") as fh:
                saved_name = storage.save(key, File(fh, name=filename))
        if saved_name != key:
            backup.file.name = saved_name
            backup.save(update_fields=["file"])
        logger.info("backup.stored key=%s bytes=%s", saved_name, backup.file_size)
        log_system_action(
            "audit.log.backupManual",
            user=user,
            metadata={"backupId": str(backup.id), "initiator": initiator},
        )
        return backup
    except Exception as exc:  # pragma: no cover - defensive logging
        backup.mark_failed(str(exc))
        if saved_name:
            try:
                storage.delete(saved_name)
            except Exception:
                logger.warning("backup.cleanup_failed key=%s", saved_name)
        log_system_action(
            "audit.log.backupFailed",
            user=user,
            metadata={
                "backupId": str(backup.id),
                "error": str(exc),
            },
        )
        raise


def restore_database_backup(backup: SystemBackup, user=None) -> Path:
    """
    Replace the live database with a stored backup. The backup is downloaded from
    the ``backups`` storage first; the current database is kept as a local
    pre-restore snapshot under BACKUP_ROOT (the two most recent are kept).
    """
    if not backup.file or not backup.file.name:
        raise ValueError("Selected backup does not contain a file.")

    db_path, engine = _ensure_sqlite_backend()
    storage = backup.file.storage
    backup_root = get_backup_root()

    with tempfile.TemporaryDirectory(prefix="crm-restore-", dir=backup_root) as tmp:
        staged = Path(tmp) / "restore.sqlite3"
        try:
            with storage.open(backup.file.name, "rb") as src, staged.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Backup file missing in storage: {backup.file.name}") from exc
        if staged.stat().st_size == 0:
            raise ValueError("Backup file is empty.")

        connections.close_all()
        snapshot_path = backup_root / f"pre-restore-{timezone.now().strftime('%Y%m%d%H%M%S')}.sqlite3"
        if db_path.exists():
            shutil.copy2(db_path, snapshot_path)
        shutil.copy2(staged, db_path)

    log_system_action(
        "audit.log.backupRestored",
        user=user,
        metadata={
            "backupId": str(backup.id),
            "engine": engine,
            "snapshot": str(snapshot_path),
        },
    )
    _clean_old_pre_restore_snapshots(keep_last=2)
    return snapshot_path


def delete_backup(backup: SystemBackup, user=None):
    backup_id = str(backup.id)
    file_name = backup.file.name if backup.file else None
    if file_name:
        # Storage delete is idempotent: a missing local file or object is fine.
        backup.file.storage.delete(file_name)
    backup.delete()
    log_system_action(
        "audit.log.backupDeleted",
        user=user,
        metadata={
            "backupId": backup_id,
            "file": file_name,
        },
    )
