"""Tests fuer ``--print-watch-dir`` (genutzt von scripts/freigabe-einrichten.ps1)."""

import sqlite3
from pathlib import Path

import app.config
from app.config import Settings


def _db_with_watch_dir(path: Path, value: str | None) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        if value is not None:
            conn.execute("INSERT INTO system_settings (key, value) VALUES ('watch_dir', ?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _run(test_settings, monkeypatch, capsys) -> tuple[int, str]:
    monkeypatch.setattr(app.config, "get_settings", lambda: test_settings)
    from app.entrypoint import main

    rc = main(["--print-watch-dir"])
    return rc, capsys.readouterr().out.strip()


def test_prints_db_setting(test_settings: Settings, tmp_path, monkeypatch, capsys):
    custom = tmp_path / "Scanner Eingang"
    _db_with_watch_dir(test_settings.sqlite_db_path, str(custom))
    rc, out = _run(test_settings, monkeypatch, capsys)
    assert rc == 0
    assert out == str(custom.resolve())


def test_falls_back_to_watch_dir(test_settings: Settings, monkeypatch, capsys):
    _db_with_watch_dir(test_settings.sqlite_db_path, None)
    rc, out = _run(test_settings, monkeypatch, capsys)
    assert rc == 0
    assert out == str(Path(test_settings.WATCH_DIR).resolve())


def test_without_database(test_settings: Settings, monkeypatch, capsys):
    rc, out = _run(test_settings, monkeypatch, capsys)
    assert rc == 0
    assert out == str(Path(test_settings.WATCH_DIR).resolve())
    assert not test_settings.sqlite_db_path.exists()  # nur lesend, nichts angelegt


def test_unc_is_printed_unchanged(test_settings: Settings, monkeypatch, capsys):
    unc = "\\\\nas\\scans\\eingang"
    _db_with_watch_dir(test_settings.sqlite_db_path, unc)
    rc, out = _run(test_settings, monkeypatch, capsys)
    assert rc == 0
    assert out == unc  # kein resolve() -> kein Netzwerkzugriff
