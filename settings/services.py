from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from django.conf import settings as project_settings
from django.core.files import File
from django.db import connections
from django.utils import timezone

from .models import SystemBackup, SystemAuditLog


def get_backup_root() -> Path:
    backup_root = Path(getattr(project_settings, "BACKUP_ROOT", project_settings.MEDIA_ROOT / "backups"))
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root


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

    try:
        with open(db_path, "rb") as src:
            backup.file.save(filename, File(src), save=False)
        backup.file_size = backup.file.size or backup.file.storage.size(backup.file.name)
        backup.mark_completed(
            file_size=backup.file_size,
            metadata={"filename": backup.file.name},
        )
        log_system_action(
            "audit.log.backupManual",
            user=user,
            metadata={"backupId": str(backup.id), "initiator": initiator},
        )
        return backup
    except Exception as exc:  # pragma: no cover - defensive logging
        backup.mark_failed(str(exc))
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
    if not backup.file:
        raise ValueError("Selected backup does not contain a file.")

    db_path, engine = _ensure_sqlite_backend()
    backup_path = Path(backup.file.path)
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
    return snapshot_path


def delete_backup(backup: SystemBackup, user=None):
    backup_id = str(backup.id)
    file_name = backup.file.name if backup.file else None
    if backup.file:
        backup.file.delete(save=False)
    backup.delete()
    log_system_action(
        "audit.log.backupDeleted",
        user=user,
        metadata={
            "backupId": backup_id,
            "file": file_name,
        },
    )

