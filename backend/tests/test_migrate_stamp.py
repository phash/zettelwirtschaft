"""B1-Guard (CODE_REVIEW): detect_stamp() darf nur existierende Alembic-Revisionen
zurueckgeben.

Frueher lieferte die Funktion fuer Legacy-DBs ohne alembic_version-Tracking die
Strings "001_add_ocr_analysis_columns", "003_add_fts5_and_saved_searches" und
"004_add_notifications_corrections_review_ext" — keine davon existiert als
Revision. `alembic upgrade head` brach danach mit "Can't locate revision" ab und
die App startete nicht mehr. Dieser Test prueft jeden betroffenen Branch gegen
die tatsaechliche Revisions-Menge der Alembic-Chain.
"""
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from migrate import detect_stamp

_BACKEND = Path(__file__).resolve().parents[1]


def _alembic_cfg() -> Config:
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    return cfg


def _valid_revisions() -> set[str]:
    return {s.revision for s in ScriptDirectory.from_config(_alembic_cfg()).walk_revisions()}


# Minimal-Schemas, die die einzelnen detect_stamp-Branches treffen — inklusive
# der drei historisch fehlerhaften (001/003/004).
_SCHEMA_CASES = {
    "001_ocr": ["CREATE TABLE processing_jobs (id INTEGER, ocr_text TEXT)"],
    "002_documents": [
        "CREATE TABLE processing_jobs (id INTEGER, ocr_text TEXT)",
        "CREATE TABLE documents (id TEXT)",
    ],
    "003_fts": [
        "CREATE TABLE documents (id TEXT)",
        "CREATE TABLE documents_fts (doc_id)",
    ],
    "004_notifications": [
        "CREATE TABLE documents (id TEXT)",
        "CREATE TABLE notifications (id TEXT)",
    ],
}


@pytest.mark.parametrize("label,setup", list(_SCHEMA_CASES.items()))
def test_detect_stamp_returns_valid_revision(tmp_path, label, setup):
    valid = _valid_revisions()
    db = tmp_path / f"legacy_{label}.db"
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        for stmt in setup:
            cur.execute(stmt)
        stamp = detect_stamp(cur)
    finally:
        conn.close()
    assert stamp is not None, f"Fall {label}: detect_stamp lieferte None"
    assert stamp in valid, (
        f"Fall {label}: ungueltige Revision {stamp!r} — nicht in {sorted(valid)}"
    )


def test_detect_stamp_none_for_empty_db(tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    try:
        stamp = detect_stamp(conn.cursor())
    finally:
        conn.close()
    assert stamp is None


def test_is_fresh_db_ignores_alembic_version(tmp_path):
    from migrate import is_fresh_db

    conn = sqlite3.connect(tmp_path / "f.db")
    try:
        cur = conn.cursor()
        assert is_fresh_db(cur) is True
        cur.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        assert is_fresh_db(cur) is True
        cur.execute("CREATE TABLE documents (id TEXT)")
        assert is_fresh_db(cur) is False
    finally:
        conn.close()


def test_ensure_default_filing_scope_is_idempotent(tmp_path):
    from migrate import ensure_default_filing_scope

    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        cur = conn.cursor()
        # Ohne Tabelle (sehr alte Legacy-DB vor 005): nichts tun.
        assert ensure_default_filing_scope(cur) is False
        cur.execute(
            "CREATE TABLE filing_scopes (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
            "slug TEXT UNIQUE, description TEXT, keywords TEXT, is_default BOOLEAN, "
            "color TEXT, created_at DATETIME)"
        )
        assert ensure_default_filing_scope(cur) is True
        assert ensure_default_filing_scope(cur) is False
        rows = cur.execute("SELECT name, slug, is_default FROM filing_scopes").fetchall()
        assert rows == [("Privat", "privat", 1)]
    finally:
        conn.close()


def test_migrate_script_bootstraps_fresh_db(tmp_path):
    """Docker-Frischinstallation: migrate.py auf leerer DB muss durchlaufen.

    Regression: `alembic upgrade head` auf leerer DB scheiterte an Migration 001
    (ALTER TABLE processing_jobs ohne CREATE) -> Backend-Container im Restart-Loop.
    """
    import os
    import subprocess
    import sys

    db = tmp_path / "fresh.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "PYTHONPATH": str(_BACKEND),
    }
    for _ in range(2):  # zweiter Lauf: idempotent, kein doppelter Seed
        result = subprocess.run(
            [sys.executable, str(_BACKEND / "migrate.py")],
            cwd=str(_BACKEND), env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        head = ScriptDirectory.from_config(_alembic_cfg()).get_current_head()
        assert cur.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
        assert cur.execute(
            "SELECT name, is_default FROM filing_scopes"
        ).fetchall() == [("Privat", 1)]
        assert _has_processing_jobs(cur)
    finally:
        conn.close()


def _has_processing_jobs(cur: sqlite3.Cursor) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='processing_jobs'")
    return cur.fetchone() is not None


# --- Echte Alembic-Kette (Upgrade-Pfad von Bestandsinstallationen) ---------


def _alembic(db: Path, *args: str) -> None:
    """alembic im Subprozess gegen eine tmp-DB (die App-Settings sind im
    Test-Prozess gecacht und zeigen auf die Dev-DB)."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "ZETTELWIRTSCHAFT_CONFIG"}
    env.update({"DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}", "PYTHONPATH": str(_BACKEND)})
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# Schema vor Migration 001 (damals per init_db/create_all angelegt).
_PRE_001_PROCESSING_JOBS = (
    "CREATE TABLE processing_jobs ("
    "id VARCHAR(36) PRIMARY KEY, original_filename VARCHAR(500), stored_filename VARCHAR(500), "
    "file_path VARCHAR(1000), file_size INTEGER, mime_type VARCHAR(100), source VARCHAR(20), "
    "status VARCHAR(20), error_message TEXT, retry_count INTEGER DEFAULT 0, "
    "created_at DATETIME, updated_at DATETIME)"
)


def test_alembic_chain_upgrades_legacy_db_without_data_loss(tmp_path):
    """Laeuft die komplette Kette 001..head auf einer Legacy-DB.

    Regressionen:
    - 005 brach auf SQLite ab (create_foreign_key ausserhalb von batch mode).
    - 010 loeschte per ON DELETE CASCADE alle processed_emails und nullte
      processing_jobs.email_account_id, weil env.py waehrend der Tabellen-
      Rebuilds PRAGMA foreign_keys=ON setzte.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(_PRE_001_PROCESSING_JOBS)
    conn.execute(
        "INSERT INTO processing_jobs (id, original_filename, stored_filename, file_path, file_size, "
        "mime_type, source, status, retry_count, created_at, updated_at) VALUES "
        "('job-0', 'alt.pdf', 'alt.pdf', '/x/alt.pdf', 1, 'application/pdf', 'UPLOAD', 'COMPLETED', 0, "
        "'2024-01-01 10:00:00', '2024-01-01 10:00:00')"
    )
    conn.commit()
    conn.close()

    _alembic(db, "upgrade", "009_add_email_accounts")

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO email_accounts (id, name, imap_host, username, encrypted_password) "
            "VALUES (1, 'Konto', 'imap.example.org', 'max', 'x')"
        )
        conn.execute(
            "INSERT INTO processing_jobs (id, original_filename, stored_filename, file_path, file_size, "
            "mime_type, source, status, retry_count, created_at, updated_at, email_account_id) VALUES "
            "('job-1', 'mail.pdf', 'mail.pdf', '/x/mail.pdf', 1, 'application/pdf', 'EMAIL', 'COMPLETED', 0, "
            "'2024-02-01 10:00:00', '2024-02-01 10:00:00', 1)"
        )
        conn.execute(
            "INSERT INTO processed_emails (email_account_id, message_id, status, processing_job_id) "
            "VALUES (1, '<m1@example.org>', 'RELEVANT', 'job-1')"
        )
        conn.commit()
        scope_count = conn.execute("SELECT count(*) FROM filing_scopes WHERE is_default = 1").fetchone()[0]
        assert scope_count == 1
    finally:
        conn.close()

    _alembic(db, "upgrade", "heads")

    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT count(*) FROM processed_emails").fetchone() == (1,)
        assert cur.execute("SELECT count(*) FROM email_accounts").fetchone() == (1,)
        assert cur.execute("SELECT count(*) FROM processing_jobs").fetchone() == (2,)
        assert cur.execute("SELECT email_account_id FROM processing_jobs WHERE id='job-1'").fetchone() == (1,)
        assert cur.execute(
            "SELECT processing_job_id FROM processed_emails"
        ).fetchone() == ("job-1",)
        assert column_type_of(cur, "email_accounts", "filing_scope_id").startswith("VARCHAR")
        assert has_column(cur, "documents", "filing_scope_id")
        assert cur.execute("PRAGMA foreign_key_check").fetchall() == []
        heads = {s.revision for s in ScriptDirectory.from_config(_alembic_cfg()).get_revisions("heads")}
        assert {r[0] for r in cur.execute("SELECT version_num FROM alembic_version")} == heads
    finally:
        conn.close()


def test_migration_005_is_rerunnable_after_partial_failure(tmp_path):
    """005 hinterliess bei einem Abbruch filing_scopes — ein erneuter Lauf
    scheiterte dann an "table filing_scopes already exists"."""
    db = tmp_path / "half.db"
    conn = sqlite3.connect(db)
    conn.execute(_PRE_001_PROCESSING_JOBS)
    conn.commit()
    conn.close()
    _alembic(db, "upgrade", "004_notifications_corrections")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE filing_scopes (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) UNIQUE NOT NULL, "
        "slug VARCHAR(200) UNIQUE NOT NULL, description TEXT, keywords TEXT, is_default BOOLEAN NOT NULL, "
        "color VARCHAR(7), created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
    )
    conn.commit()
    conn.close()

    _alembic(db, "upgrade", "005_add_filing_scopes")

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT name FROM filing_scopes WHERE is_default = 1").fetchall() == [("Privat",)]
        fks = conn.execute("PRAGMA foreign_key_list(documents)").fetchall()
        assert any(row[2] == "filing_scopes" and row[3] == "filing_scope_id" for row in fks)
    finally:
        conn.close()


def column_type_of(cur: sqlite3.Cursor, table: str, column: str) -> str:
    from migrate import column_type

    return column_type(cur, table, column) or ""


def has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    from migrate import has_column as _has_column

    return _has_column(cur, table, column)


def test_revision_chain_is_linear():
    """Genau ein Head — parallel entstandene Migrationen muessen verkettet sein,
    sonst bricht `alembic upgrade head`/`stamp head` (migrate.py) ab."""
    heads = ScriptDirectory.from_config(_alembic_cfg()).get_heads()
    assert len(heads) == 1, f"mehrere Heads: {heads}"


def test_detect_stamp_recognizes_background_tasks_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "bg.db")
    try:
        cur = conn.cursor()
        cur.execute("CREATE TABLE chat_messages (id TEXT, filing_scope_id TEXT REFERENCES filing_scopes(id))")
        cur.execute("CREATE TABLE background_tasks (id TEXT)")
        assert detect_stamp(cur) == "014_background_tasks"
    finally:
        conn.close()


def test_fresh_db_downgrades_to_013_and_upgrades_again(tmp_path):
    """Downgrade-Pfad der 014+-Migrationen: head -> 013 -> head ohne Fehler."""
    import os
    import subprocess
    import sys

    db = tmp_path / "roundtrip.db"
    env = {k: v for k, v in os.environ.items() if k != "ZETTELWIRTSCHAFT_CONFIG"}
    env.update({"DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}", "PYTHONPATH": str(_BACKEND)})
    result = subprocess.run(
        [sys.executable, str(_BACKEND / "migrate.py")],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    _alembic(db, "downgrade", "013_fk_indexes_consistency")
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        assert not has_column(cur, "processing_jobs", "document_id")
        assert cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='background_tasks'"
        ).fetchone() is None
    finally:
        conn.close()

    _alembic(db, "upgrade", "head")
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        head = ScriptDirectory.from_config(_alembic_cfg()).get_current_head()
        assert cur.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
        assert has_column(cur, "processing_jobs", "document_id")
        assert cur.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_legacy_fk_violation_survives_table_rebuild(tmp_path):
    """Altlast-FK-Verletzungen (FKs waren bis v1.3 aus) duerfen das Update nicht
    blockieren — auch wenn eine Migration die betroffene Tabelle neu aufbaut.

    batch_alter_table kopiert die Zeilen per INSERT SELECT; Tabellen ohne
    INTEGER-PK bekommen dabei neue rowids. Der Vorher/Nachher-Vergleich in
    env.py darf die Verletzung deshalb nicht an der rowid festmachen, sonst gilt
    dieselbe Altlast nach dem Rebuild als "neu" und die Migration bricht ab.
    """
    import os
    import subprocess
    import sys

    db = tmp_path / "legacy_fk.db"
    env = {k: v for k, v in os.environ.items() if k != "ZETTELWIRTSCHAFT_CONFIG"}
    env.update({"DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}", "PYTHONPATH": str(_BACKEND)})
    result = subprocess.run(
        [sys.executable, str(_BACKEND / "migrate.py")],
        cwd=str(_BACKEND), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _alembic(db, "downgrade", "013_fk_indexes_consistency")

    conn = sqlite3.connect(db)
    try:
        for job_id in ("job-a", "job-b", "job-c"):
            conn.execute(
                "INSERT INTO processing_jobs (id, original_filename, stored_filename, file_path, file_type, "
                "file_size_bytes, source, status, retry_count, created_at, updated_at, email_account_id) VALUES "
                f"('{job_id}', 'm.pdf', 'm.pdf', '/x/m.pdf', 'pdf', 1, 'EMAIL', 'COMPLETED', 0, "
                "'2024-02-01 10:00:00', '2024-02-01 10:00:00', 99)"
            )
        # rowid-Luecke: die verbleibende Altlast hat rowid 3, nach dem Rebuild 1.
        conn.execute("DELETE FROM processing_jobs WHERE id IN ('job-a', 'job-b')")
        conn.commit()
        assert len(conn.execute("PRAGMA foreign_key_check(processing_jobs)").fetchall()) == 1
    finally:
        conn.close()

    _alembic(db, "upgrade", "head")

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT id, email_account_id FROM processing_jobs").fetchall() == [("job-c", 99)]
        head = ScriptDirectory.from_config(_alembic_cfg()).get_current_head()
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
    finally:
        conn.close()
