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


def _valid_revisions() -> set[str]:
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    return {s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()}


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
