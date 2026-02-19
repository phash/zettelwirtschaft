"""Backup-Service: Datenbank und Konfiguration sichern und wiederherstellen."""

import asyncio
import logging
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)


def _backup_dir(settings: Settings) -> Path:
    p = Path(settings.ARCHIVE_DIR).parent / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_path(settings: Settings) -> Path:
    url = settings.DATABASE_URL
    # sqlite+aiosqlite:///./data/zettelwirtschaft.db -> ./data/zettelwirtschaft.db
    path_str = url.split("///")[-1]
    return Path(path_str)


def create_backup(settings: Settings, include_documents: bool = False) -> str:
    """Erstellt ein Backup als ZIP-Datei. Gibt den Dateipfad zurueck."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_type = "full" if include_documents else "db"
    filename = f"backup_{backup_type}_{timestamp}.zip"
    backup_path = _backup_dir(settings) / filename

    db_file = _db_path(settings)

    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Datenbank
        if db_file.exists():
            zf.write(db_file, "database/zettelwirtschaft.db")

        # .env Konfiguration
        env_file = Path(".env")
        if env_file.exists():
            zf.write(env_file, "config/.env")

        # Dokumente (optional, bei Full-Backup)
        if include_documents:
            archive_dir = Path(settings.ARCHIVE_DIR)
            if archive_dir.exists():
                for file_path in archive_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = f"documents/{file_path.relative_to(archive_dir)}"
                        zf.write(file_path, arcname)

    logger.info("Backup erstellt: %s (%.1f MB)", backup_path, backup_path.stat().st_size / 1024 / 1024)
    return str(backup_path)


def list_backups(settings: Settings) -> list[dict]:
    """Listet vorhandene Backups."""
    backup_dir = _backup_dir(settings)
    backups = []
    for f in sorted(backup_dir.glob("backup_*.zip"), reverse=True):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "path": str(f),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_full": "_full_" in f.name,
        })
    return backups


def cleanup_old_backups(settings: Settings, keep_daily: int = 7, keep_weekly: int = 4) -> int:
    """Loescht alte Backups, behaelt die neuesten."""
    backup_dir = _backup_dir(settings)
    all_backups = sorted(backup_dir.glob("backup_db_*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)

    to_keep = keep_daily + keep_weekly
    removed = 0
    for f in all_backups[to_keep:]:
        f.unlink()
        removed += 1

    if removed:
        logger.info("%d alte Backups geloescht", removed)
    return removed


def get_system_info(settings: Settings) -> dict:
    """Sammelt System-Informationen."""
    db_file = _db_path(settings)
    archive_dir = Path(settings.ARCHIVE_DIR)
    upload_dir = Path(settings.UPLOAD_DIR)

    # Speichernutzung berechnen
    archive_size = sum(f.stat().st_size for f in archive_dir.rglob("*") if f.is_file()) if archive_dir.exists() else 0
    upload_size = sum(f.stat().st_size for f in upload_dir.rglob("*") if f.is_file()) if upload_dir.exists() else 0
    db_size = db_file.stat().st_size if db_file.exists() else 0

    # Disk space
    try:
        total, used, free = shutil.disk_usage(str(archive_dir.parent))
    except Exception:
        total, used, free = 0, 0, 0

    return {
        "database_size_bytes": db_size,
        "archive_size_bytes": archive_size,
        "upload_size_bytes": upload_size,
        "disk_total_bytes": total,
        "disk_used_bytes": used,
        "disk_free_bytes": free,
    }


async def run_auto_backup(session_factory, settings: Settings) -> None:
    """Background-Task: Erstellt taeglich ein automatisches Backup."""
    logger.info("Auto-Backup-Service gestartet")
    while True:
        try:
            # Backup erstellen
            path = create_backup(settings, include_documents=False)
            logger.info("Auto-Backup erstellt: %s", path)
            # Alte aufraeumen
            cleanup_old_backups(settings)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Fehler beim Auto-Backup")
        # 24 Stunden warten
        await asyncio.sleep(86400)
