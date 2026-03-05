from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from django.conf import settings as project_settings
from django.core.files.storage import default_storage
from django.db import connections
from django.utils import timezone

from .models import SystemBackup, SystemAuditLog


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


def create_database_backup(initiator: str = SystemBackup.Initiator.MANUAL, user=None, notes: str = "") -> SystemBackup:
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

    dest_path = None
    try:
        # Write file to the same location we use for read/delete: MEDIA_ROOT/backups/
        backup_root = get_backup_root()
        dest_path = backup_root / filename
        shutil.copy2(db_path, dest_path)
        # Store path relative to MEDIA_ROOT (assign string so DB persists it; file already on disk)
        backup.file = "backups/" + filename
        backup.file_size = dest_path.stat().st_size
        backup.save(update_fields=["file", "file_size"])
        backup.mark_completed(
            file_size=backup.file_size,
            metadata={"filename": backup.file.name},
        )
        # Re-copy DB so the backup file contains this completed record (with file path).
        # Otherwise restore would load a snapshot where the record was still in_progress/no file.
        shutil.copy2(db_path, dest_path)
        log_system_action(
            "audit.log.backupManual",
            user=user,
            metadata={"backupId": str(backup.id), "initiator": initiator},
        )
        return backup
    except Exception as exc:  # pragma: no cover - defensive logging
        backup.mark_failed(str(exc))
        if dest_path is not None and dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
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
    if not backup.file or not backup.file.name:
        raise ValueError("Selected backup does not contain a file.")

    db_path, engine = _ensure_sqlite_backend()
    # Same path as create/delete: MEDIA_ROOT + file.name
    media_root = Path(project_settings.MEDIA_ROOT)
    backup_path = media_root / backup.file.name
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file missing on disk: {backup_path}")

    connections.close_all()
    backup_root = get_backup_root()

    snapshot_path = backup_root / f"pre-restore-{timezone.now().strftime('%Y%m%d%H%M%S')}.sqlite3"
    if db_path.exists():
        shutil.copy2(db_path, snapshot_path)

    shutil.copy2(backup_path, db_path)

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
    paths_to_remove = []
    if backup.file and backup.file.name:
        try:
            paths_to_remove.append(default_storage.path(backup.file.name))
        except Exception:
            pass
        backup.file.delete(save=False)
    backup.delete()
    # Ensure the file is removed from disk (Django storage may not delete in some configs)
    media_root = getattr(project_settings, "MEDIA_ROOT", None)
    if media_root and file_name:
        paths_to_remove.append(os.path.join(str(media_root), file_name))
    for physical_path in paths_to_remove:
        if physical_path and os.path.isfile(physical_path):
            try:
                os.remove(physical_path)
            except OSError:
                pass
    log_system_action(
        "audit.log.backupDeleted",
        user=user,
        metadata={
            "backupId": backup_id,
            "file": file_name,
        },
    )

