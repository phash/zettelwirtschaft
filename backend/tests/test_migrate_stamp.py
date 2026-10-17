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
