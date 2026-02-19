"""Tests fuer Backup-Service."""

import pytest
from pathlib import Path

from app.config import get_settings
from app.services.backup_service import create_backup, get_system_info, list_backups


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
