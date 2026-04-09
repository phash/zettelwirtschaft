"""Tests fuer Backup-Service."""

import os
import time

import pytest
from pathlib import Path

from app.config import Settings, get_settings
from app.services.backup_service import cleanup_old_backups, create_backup, get_system_info, list_backups


class TestBackup:
    def test_create_db_backup(self):
        """Erstellt ein DB-only Backup."""
        settings = get_settings()
        path = create_backup(settings, include_documents=False)
        assert Path(path).exists()
        assert "backup_db_" in path
        assert path.endswith(".zip")
        # Aufraeumen
        Path(path).unlink()

    def test_list_backups(self):
        """Listet Backups auf."""
        settings = get_settings()
        backups = list_backups(settings)
        assert isinstance(backups, list)


class TestSystemInfo:
    def test_get_system_info(self):
        """Gibt System-Informationen zurueck."""
        settings = get_settings()
        info = get_system_info(settings)
        assert "database_size_bytes" in info
        assert "disk_total_bytes" in info
        assert "disk_free_bytes" in info


class TestCleanupOldBackups:
    def _make_backup_files(self, backup_dir: Path, count: int) -> list[Path]:
        """Erstellt count Backup-Dateien mit aufsteigenden mtimes."""
        backup_dir.mkdir(parents=True, exist_ok=True)
        files = []
        now = time.time()
        for i in range(count):
            f = backup_dir / f"backup_db_{i:02d}.zip"
            f.write_bytes(b"fake")
            # Aelteste Datei bekommt niedrigste mtime
            mtime = now - (count - i) * 3600
            os.utime(str(f), (mtime, mtime))
            files.append(f)
        return files

    def test_cleanup_no_backups(self, test_settings: Settings):
        """Leeres Backup-Verzeichnis gibt 0 zurueck."""
        # Backup-Verzeichnis existiert, ist aber leer
        backup_dir = Path(test_settings.ARCHIVE_DIR).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        removed = cleanup_old_backups(test_settings)
        assert removed == 0

    def test_cleanup_within_limit(self, test_settings: Settings):
        """Weniger Backups als Limit: nichts wird geloescht."""
        backup_dir = Path(test_settings.ARCHIVE_DIR).parent / "backups"
        files = self._make_backup_files(backup_dir, 5)

        # Default: keep_daily=7, keep_weekly=4 -> to_keep=11
        removed = cleanup_old_backups(test_settings)
        assert removed == 0
        # Alle 5 Dateien existieren noch
        for f in files:
            assert f.exists()

    def test_cleanup_over_limit(self, test_settings: Settings):
        """Mehr Backups als Limit: ueberschuessige werden geloescht."""
        backup_dir = Path(test_settings.ARCHIVE_DIR).parent / "backups"
        files = self._make_backup_files(backup_dir, 15)

        # keep_daily=3, keep_weekly=2 -> to_keep=5, also 10 loeschen
        removed = cleanup_old_backups(test_settings, keep_daily=3, keep_weekly=2)
        assert removed == 10

        remaining = list(backup_dir.glob("backup_db_*.zip"))
        assert len(remaining) == 5

    def test_cleanup_keeps_newest(self, test_settings: Settings):
        """Die neuesten Backups ueberleben den Cleanup."""
        backup_dir = Path(test_settings.ARCHIVE_DIR).parent / "backups"
        files = self._make_backup_files(backup_dir, 5)

        # keep_daily=1, keep_weekly=1 -> to_keep=2, also 3 loeschen
        removed = cleanup_old_backups(test_settings, keep_daily=1, keep_weekly=1)
        assert removed == 3

        remaining = sorted(backup_dir.glob("backup_db_*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
        assert len(remaining) == 2

        # Die beiden neuesten Dateien (hoechste mtime) muessen ueberleben
        # files[4] hat die neueste mtime, files[3] die zweitneueste
        remaining_names = {f.name for f in remaining}
        assert files[4].name in remaining_names, "Neueste Datei muss ueberleben"
        assert files[3].name in remaining_names, "Zweitneueste Datei muss ueberleben"
