from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_KEEP_BACKUPS = 20
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S%f"


def create_backup(database_path: Path, backups_dir: Path) -> Path | None:
    """Copy the live database into backups_dir using SQLite's online backup API.

    Safe to call while the database is open for writes. Prunes old backups,
    keeping only the most recent ones.
    """
    if not database_path.exists():
        return None
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    destination = backups_dir / f"cuentas-{timestamp}.sqlite3"
    source_conn = sqlite3.connect(str(database_path))
    try:
        dest_conn = sqlite3.connect(str(destination))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    _prune_old_backups(backups_dir)
    return destination


def _prune_old_backups(backups_dir: Path, *, keep: int = _KEEP_BACKUPS) -> None:
    backups = list_backups(backups_dir)
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)


def list_backups(backups_dir: Path) -> list[Path]:
    if not backups_dir.exists():
        return []
    return sorted(
        (path for path in backups_dir.glob("cuentas-*.sqlite3") if path.is_file()),
        key=lambda path: path.name,
        reverse=True,
    )


def _integrity_ok(path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(path))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(result) and result[0] == "ok"
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def restore_backup(backup_path: Path, database_path: Path) -> None:
    """Restore database_path from backup_path, verifying integrity first.

    A safety copy of the current database is made before overwriting it, and
    restored back automatically if the chosen backup turns out to be invalid.
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    if not _integrity_ok(backup_path):
        raise ValueError(f"Backup file failed integrity check: {backup_path}")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    pre_restore_copy: Path | None = None
    if database_path.exists():
        timestamp = datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)
        pre_restore_copy = database_path.parent.parent / "backups" / f"pre-restore-{timestamp}.sqlite3"
        pre_restore_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(database_path, pre_restore_copy)

    shutil.copy2(backup_path, database_path)

    if not _integrity_ok(database_path):
        if pre_restore_copy is not None:
            shutil.copy2(pre_restore_copy, database_path)
        raise ValueError("Restored database failed integrity check; reverted to the previous state")
