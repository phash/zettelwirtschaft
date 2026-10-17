"""Tests fuer Backup-Service (immer gegen das isolierte test_settings-tmp)."""

import os
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services import backup_service
from app.services.backup_service import (
    cleanup_old_backups,
    create_backup,
    get_system_info,
    list_backups,
    seconds_until_next_auto_backup,
)


def _make_db(settings: Settings) -> Path:
    db = settings.sqlite_db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
    finally:
        conn.close()
    return db


def _backup_dir(settings: Settings) -> Path:
    return settings.data_dir / "backups"


class TestCreateBackup:
    def test_create_db_backup(self, test_settings: Settings):
        _make_db(test_settings)
        path = Path(create_backup(test_settings, include_documents=False))
        assert path.parent == _backup_dir(test_settings)
        assert path.name.startswith("backup_db_") and path.suffix == ".zip"
        with zipfile.ZipFile(path) as zf:
            assert zf.namelist() == ["database/zettelwirtschaft.db"]

    def test_missing_database_raises_and_writes_nothing(self, test_settings: Settings, tmp_path):
        """Regression: fehlende DB ergab ein leeres, als Erfolg gemeldetes ZIP."""
        out = tmp_path / "out"
        with pytest.raises(FileNotFoundError, match="Datenbankdatei"):
            create_backup(test_settings, out_dir=out)
        assert not out.exists() or list(out.iterdir()) == []

    def test_failed_backup_leaves_no_archive(self, test_settings: Settings, monkeypatch):
        """Regression: ein Abbruch mitten im Schreiben hinterliess ein gueltig
        aussehendes, unvollstaendiges backup_*.zip, das in die Rotation einging."""
        _make_db(test_settings)
        doc = Path(test_settings.ARCHIVE_DIR) / "a.pdf"
        doc.write_bytes(b"%PDF")
        real_write = zipfile.ZipFile.write

        def flaky_write(self, filename, arcname=None, *args, **kwargs):
            if str(arcname).startswith("documents/"):
                raise OSError("Datei gesperrt")
            return real_write(self, filename, arcname, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "write", flaky_write)
        with pytest.raises(OSError):
            create_backup(test_settings, include_documents=True)
        assert list(_backup_dir(test_settings).iterdir()) == []

    def test_full_backup_includes_documents(self, test_settings: Settings):
        _make_db(test_settings)
        doc = Path(test_settings.ARCHIVE_DIR) / "privat" / "2024" / "a.pdf"
        doc.parent.mkdir(parents=True)
        doc.write_bytes(b"%PDF")
        path = create_backup(test_settings, include_documents=True)
        with zipfile.ZipFile(path) as zf:
            assert "documents/privat/2024/a.pdf" in zf.namelist()

    def test_filename_uses_local_timezone(self, test_settings: Settings):
        _make_db(test_settings)
        settings = test_settings.model_copy(update={"TIMEZONE": "Pacific/Kiritimati"})  # UTC+14
        name = Path(create_backup(settings)).name
        expected_day = datetime.now(settings.local_tz).strftime("%Y%m%d")
        assert name.startswith(f"backup_db_{expected_day}_")

    def test_two_backups_in_the_same_second_do_not_overwrite(self, test_settings: Settings, monkeypatch):
        """Regression: zwei Laeufe in derselben Sekunde (Auto-Backup + manuell)
        ergaben denselben Dateinamen — os.replace ueberschrieb das erste."""
        _make_db(test_settings)
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=test_settings.local_tz)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr(backup_service, "datetime", _FrozenDatetime)
        first = Path(create_backup(test_settings))
        second = Path(create_backup(test_settings))
        assert first != second
        assert first.is_file() and second.is_file()
        assert second.name.startswith("backup_db_20260102_030405")
        assert {b["filename"] for b in list_backups(test_settings)} == {first.name, second.name}


class TestListBackups:
    def test_sorted_by_mtime_without_paths(self, test_settings: Settings):
        d = _backup_dir(test_settings)
        d.mkdir(parents=True)
        full_old = d / "backup_full_20240101_000000.zip"
        db_new = d / "backup_db_20240101_000000.zip"
        (d / f"{backup_service.PARTIAL_PREFIX}backup_db_x.zip").write_bytes(b"PK")
        for f in (full_old, db_new):
            f.write_bytes(b"PK")
        now = time.time()
        os.utime(full_old, (now - 100, now - 100))
        os.utime(db_new, (now, now))
        backups = list_backups(test_settings)
        assert [b["filename"] for b in backups] == [db_new.name, full_old.name]
        assert all("path" not in b for b in backups)
        assert datetime.fromisoformat(backups[0]["created_at"]).tzinfo is not None


class TestSystemInfo:
    def test_get_system_info(self, test_settings: Settings):
        _make_db(test_settings)
        info = get_system_info(test_settings)
        assert info["database_size_bytes"] > 0
        assert "disk_total_bytes" in info
        assert "disk_free_bytes" in info


class TestCleanupOldBackups:
    def _make(self, d: Path, name: str, when: datetime) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_bytes(b"fake")
        ts = when.timestamp()
        os.utime(f, (ts, ts))
        return f

    def test_cleanup_no_backups(self, test_settings: Settings):
        assert cleanup_old_backups(test_settings) == 0

    def test_many_backups_on_one_day_do_not_push_out_older_days(self, test_settings: Settings):
        """Regression: Neustarts erzeugten mehrere Backups pro Tag; die flache
        "neueste 11"-Regel loeschte dadurch aeltere Tage."""
        d = _backup_dir(test_settings)
        tz = test_settings.local_tz
        today = datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
        same_day = [self._make(d, f"backup_db_today_{i:02d}.zip", today - timedelta(minutes=i)) for i in range(10)]
        older = [self._make(d, f"backup_db_day{i}.zip", today - timedelta(days=i)) for i in range(1, 7)]

        cleanup_old_backups(test_settings, keep_daily=7, keep_weekly=4)
        remaining = {f.name for f in d.glob("backup_db_*.zip")}
        assert same_day[0].name in remaining  # neuestes von heute
        assert all(f.name in remaining for f in older)  # alle aelteren Tage
        assert sum(1 for f in same_day if f.name in remaining) == 1

    def test_weekly_retention_keeps_one_per_week(self, test_settings: Settings):
        d = _backup_dir(test_settings)
        tz = test_settings.local_tz
        base = datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
        files = [self._make(d, f"backup_db_{i:02d}.zip", base - timedelta(days=i)) for i in range(60)]

        cleanup_old_backups(test_settings, keep_daily=7, keep_weekly=4)
        remaining = sorted(d.glob("backup_db_*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
        assert all(f in remaining for f in files[:7])  # 7 Tage
        weeks = {datetime.fromtimestamp(f.stat().st_mtime, tz).isocalendar()[:2] for f in remaining}
        assert len(weeks) == 4  # innerhalb von 4 Kalenderwochen
        assert len(remaining) <= 7 + 4

    def test_full_backups_limited_separately(self, test_settings: Settings):
        d = _backup_dir(test_settings)
        now = datetime.now(test_settings.local_tz)
        fulls = [self._make(d, f"backup_full_{i}.zip", now - timedelta(hours=i)) for i in range(5)]
        assert cleanup_old_backups(test_settings, keep_full=3) == 2
        assert [f.exists() for f in fulls] == [True, True, True, False, False]

    def test_stale_partial_files_are_removed(self, test_settings: Settings):
        d = _backup_dir(test_settings)
        now = datetime.now(test_settings.local_tz)
        stale = self._make(d, f"{backup_service.PARTIAL_PREFIX}backup_full_a.zip", now - timedelta(hours=2))
        running = self._make(d, f"{backup_service.PARTIAL_PREFIX}backup_full_b.zip", now)
        cleanup_old_backups(test_settings)
        assert not stale.exists()
        assert running.exists()


class TestAutoBackupSchedule:
    def test_due_without_backups(self, test_settings: Settings):
        assert seconds_until_next_auto_backup(test_settings) == 0

    def test_recent_backup_postpones_next_run(self, test_settings: Settings):
        """Regression: jeder Neustart erzeugte sofort ein weiteres Backup."""
        d = _backup_dir(test_settings)
        d.mkdir(parents=True)
        f = d / "backup_db_x.zip"
        f.write_bytes(b"PK")
        now = time.time()
        os.utime(f, (now - 3600, now - 3600))
        wait = seconds_until_next_auto_backup(test_settings, now=now)
        assert wait == pytest.approx(23 * 3600, abs=5)

    def test_old_backup_is_due(self, test_settings: Settings):
        d = _backup_dir(test_settings)
        d.mkdir(parents=True)
        f = d / "backup_db_x.zip"
        f.write_bytes(b"PK")
        now = time.time()
        os.utime(f, (now - 25 * 3600, now - 25 * 3600))
        assert seconds_until_next_auto_backup(test_settings, now=now) == 0
