"""Tests fuer den --backup CLI-Modus des Native-Entrypoints (app/entrypoint.py)."""

import sqlite3
import zipfile
from pathlib import Path

from app.config import Settings


def _make_sqlite_db(path: Path) -> None:
    """Legt eine minimale, gueltige SQLite-DB am Zielpfad an."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
    finally:
        conn.close()


def test_backup_cli_creates_db_zip(test_settings: Settings, monkeypatch, capsys):
    db_path = Path(test_settings.DATABASE_URL.split("///")[-1])
    _make_sqlite_db(db_path)

    import app.config
    monkeypatch.setattr(app.config, "get_settings", lambda: test_settings)

    from app.entrypoint import main
    rc = main(["--backup"])

    out = capsys.readouterr().out.strip()
    assert rc == 0, "Exit-Code muss 0 sein"
    zip_path = Path(out)
    assert zip_path.exists(), f"gedruckter Pfad existiert nicht: {out}"
    assert "backup_db_" in zip_path.name
    with zipfile.ZipFile(zip_path) as zf:
        assert "database/zettelwirtschaft.db" in zf.namelist()


def test_backup_cli_full_includes_documents(test_settings: Settings, monkeypatch, capsys):
    db_path = Path(test_settings.DATABASE_URL.split("///")[-1])
    _make_sqlite_db(db_path)
    doc = Path(test_settings.ARCHIVE_DIR) / "2024" / "rechnung.pdf"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(b"%PDF-1.4 fake")

    import app.config
    monkeypatch.setattr(app.config, "get_settings", lambda: test_settings)

    from app.entrypoint import main
    rc = main(["--backup", "--full"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    zip_path = Path(out)
    assert "backup_full_" in zip_path.name
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(n.startswith("documents/") for n in names), names


def test_backup_cli_out_dir(test_settings: Settings, monkeypatch, capsys, tmp_path):
    """--out-dir schreibt das Backup ausserhalb des Datenordners (Uninstaller-Pfad)."""
    db_path = Path(test_settings.DATABASE_URL.split("///")[-1])
    _make_sqlite_db(db_path)
    out_dir = tmp_path / "safe-backups"  # NICHT der Default <archive>/../backups

    import app.config
    monkeypatch.setattr(app.config, "get_settings", lambda: test_settings)

    from app.entrypoint import main
    rc = main(["--backup", "--out-dir", str(out_dir)])

    printed = capsys.readouterr().out.strip()
    assert rc == 0
    zip_path = Path(printed)
    assert zip_path.exists()
    assert zip_path.parent == out_dir, f"Backup nicht im out-dir: {zip_path}"
