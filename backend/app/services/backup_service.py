"""Backup-Service: Datenbank (+ optional Dokumente) als ZIP sichern."""

import asyncio
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)

AUTO_BACKUP_INTERVAL_SECONDS = 24 * 60 * 60
# Halb geschriebene Archive tragen dieses Praefix, bis sie vollstaendig sind
# (os.replace auf den finalen Namen). Sie zaehlen nie als Backup.
PARTIAL_PREFIX = ".partial_"

# Serialisiert Backups innerhalb des Prozesses (Auto-Backup vs. manuell), damit
# Namenswahl (freier Dateiname, siehe create_backup) und Schreiben atomar sind.
_backup_lock = threading.Lock()


def backup_dir(settings: Settings) -> Path:
    """Standard-Zielordner ``<Datenordner>/backups`` (wird angelegt)."""
    p = settings.data_dir / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _backups_newest_first(directory: Path, pattern: str = "backup_*.zip") -> list[Path]:
    """Backups nach Aenderungszeit, neueste zuerst — gemeinsame Sortierung fuer
    Liste, Aufraeumen und Auto-Backup (der Dateiname taugt nicht: 'backup_full_'
    sortiert hinter 'backup_db_')."""
    files = [f for f in directory.glob(pattern) if f.is_file()]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def create_backup(
    settings: Settings,
    include_documents: bool = False,
    out_dir: str | Path | None = None,
) -> str:
    """Erstellt ein Backup als ZIP-Datei. Gibt den Dateipfad zurueck.

    out_dir: optionaler Zielordner ausserhalb des Datenordners. Wird z.B. vom
    Uninstaller genutzt, damit das Sicherungs-Backup das Loeschen des
    Datenordners ueberlebt. Default: <Datenordner>/backups.

    Wirft FileNotFoundError, wenn die Datenbankdatei fehlt (z.B. falsche
    config.toml) — ein "erfolgreiches" leeres Backup waere schlimmer als ein
    Fehler. Das ZIP entsteht zuerst als ``.partial_*``-Datei und bekommt erst
    nach vollstaendigem Schreiben seinen Namen: ein Abbruch hinterlaesst kein
    gueltig aussehendes, unvollstaendiges Backup.
    """
    db_file = settings.sqlite_db_path
    if not db_file.is_file():
        raise FileNotFoundError(f"Datenbankdatei nicht gefunden: {db_file.resolve()}")

    if out_dir:
        target_dir = Path(out_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = backup_dir(settings)

    with _backup_lock:
        timestamp = datetime.now(settings.local_tz).strftime("%Y%m%d_%H%M%S")
        backup_type = "full" if include_documents else "db"
        filename = f"backup_{backup_type}_{timestamp}.zip"
        # Zweiter Lauf in derselben Sekunde (Lock serialisiert nur): Zaehler
        # anhaengen, sonst ueberschriebe os.replace das erste Backup.
        suffix = 1
        while (target_dir / filename).exists():
            suffix += 1
            filename = f"backup_{backup_type}_{timestamp}_{suffix}.zip"
        backup_path = target_dir / filename
        partial_path = target_dir / f"{PARTIAL_PREFIX}{filename}"

        try:
            with zipfile.ZipFile(partial_path, "w", zipfile.ZIP_DEFLATED) as zf:
                _write_database(zf, db_file)
                # .env/config.toml werden NICHT ins Backup aufgenommen (enthalten
                # Secrets wie PIN_CODE, EMAIL_ENCRYPTION_KEY).
                if include_documents:
                    archive_dir = Path(settings.ARCHIVE_DIR)
                    if archive_dir.exists():
                        for file_path in archive_dir.rglob("*"):
                            if file_path.is_file():
                                arcname = f"documents/{file_path.relative_to(archive_dir).as_posix()}"
                                zf.write(file_path, arcname)
            os.replace(partial_path, backup_path)
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise

    logger.info("Backup erstellt: %s (%.1f MB)", backup_path, backup_path.stat().st_size / 1024 / 1024)
    return str(backup_path)


def _write_database(zf: zipfile.ZipFile, db_file: Path) -> None:
    """Konsistenter DB-Snapshot ueber die SQLite-Backup-API.

    Temp-Snapshot ins System-Temp, NICHT ins Zielverzeichnis: bei einem Crash
    zwischen close() und unlink() bliebe sonst eine vollstaendige DB-Kopie
    ausserhalb des Datenordners liegen (z.B. im Documents-Backup-Ordner beim
    Uninstall). System-Temp wird vom OS aufgeraeumt. (Review M3)
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="zw_backup_")
    os.close(fd)
    snapshot = Path(tmp_name)
    try:
        src = sqlite3.connect(str(db_file))
        try:
            dst = sqlite3.connect(str(snapshot))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        zf.write(snapshot, "database/zettelwirtschaft.db")
    finally:
        snapshot.unlink(missing_ok=True)


def list_backups(settings: Settings) -> list[dict]:
    """Listet vorhandene Backups, neueste zuerst.

    Bewusst nur Dateinamen, keine absoluten Serverpfade (enthalten u.a. den
    Windows-Benutzernamen). ``created_at`` mit UTC-Offset, damit der Browser
    korrekt in Ortszeit umrechnet.
    """
    backups = []
    for f in _backups_newest_first(backup_dir(settings)):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "is_full": f.name.startswith("backup_full_"),
        })
    return backups


def _retained(files: list[Path], keep_daily: int, keep_weekly: int, settings: Settings) -> set[Path]:
    """Grossvater-Vater-Prinzip: das neueste Backup je Tag fuer die letzten
    ``keep_daily`` Tage mit Backup und je Kalenderwoche fuer die letzten
    ``keep_weekly`` Wochen mit Backup (``files`` neueste zuerst)."""
    keep: set[Path] = set()
    days: set = set()
    weeks: set = set()
    for f in files:
        local = datetime.fromtimestamp(f.stat().st_mtime, tz=settings.local_tz)
        day = local.date()
        week = local.isocalendar()[:2]
        if day not in days and len(days) < keep_daily:
            days.add(day)
            keep.add(f)
        if week not in weeks and len(weeks) < keep_weekly:
            weeks.add(week)
            keep.add(f)
    return keep


def cleanup_old_backups(
    settings: Settings, keep_daily: int = 7, keep_weekly: int = 4, keep_full: int = 3
) -> int:
    """Loescht alte Backups nach Aufbewahrungsregel.

    - DB-Backups: je Tag das neueste fuer ``keep_daily`` Tage + je Woche das
      neueste fuer ``keep_weekly`` Wochen (mehrere Backups am selben Tag, etwa
      durch Neustarts, verdraengen so keine aelteren Tage mehr).
    - Voll-Backups (backup_full_*.zip, enthalten das komplette Archiv und sind
      oft hunderte MB gross): die neuesten ``keep_full`` (Review M1).
    - Liegengebliebene ``.partial_*``-Dateien abgebrochener Laeufe (aelter als
      eine Stunde, damit kein gerade laufendes Backup getroffen wird).
    """
    directory = backup_dir(settings)
    removed = 0

    db_backups = _backups_newest_first(directory, "backup_db_*.zip")
    keep = _retained(db_backups, keep_daily, keep_weekly, settings)
    for f in db_backups:
        if f not in keep:
            f.unlink()
            removed += 1
    for f in _backups_newest_first(directory, "backup_full_*.zip")[keep_full:]:
        f.unlink()
        removed += 1
    stale_before = time.time() - 3600
    for f in directory.glob(f"{PARTIAL_PREFIX}*"):
        if f.is_file() and f.stat().st_mtime < stale_before:
            f.unlink(missing_ok=True)
            removed += 1

    if removed:
        logger.info("%d alte Backups geloescht", removed)
    return removed


def seconds_until_next_auto_backup(settings: Settings, now: float | None = None) -> float:
    """0 wenn ein Auto-Backup faellig ist, sonst Sekunden bis dahin.

    Faellig = kein DB-Backup juenger als 24 h. So erzeugt nicht jeder
    Neustart des Dienstes/Containers ein weiteres Backup.
    """
    newest = next(iter(_backups_newest_first(backup_dir(settings), "backup_db_*.zip")), None)
    if newest is None:
        return 0.0
    now = time.time() if now is None else now
    age = now - newest.stat().st_mtime
    return max(0.0, AUTO_BACKUP_INTERVAL_SECONDS - age)


def get_system_info(settings: Settings) -> dict:
    """Sammelt System-Informationen."""
    db_file = settings.sqlite_db_path
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
    """Background-Task: taegliches DB-Backup (nur wenn das letzte >= 24 h alt ist)."""
    logger.info("Auto-Backup-Service gestartet")
    while True:
        try:
            wait = await asyncio.to_thread(seconds_until_next_auto_backup, settings)
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            path = await asyncio.to_thread(create_backup, settings, False)
            logger.info("Auto-Backup erstellt: %s", path)
            await asyncio.to_thread(cleanup_old_backups, settings)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Fehler beim Auto-Backup")
            # Nicht in einer Fehlerschleife busy-loopen (z.B. Platte voll).
            await asyncio.sleep(AUTO_BACKUP_INTERVAL_SECONDS)
