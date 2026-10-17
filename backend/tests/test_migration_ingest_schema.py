"""Migrationen 015_job_document_id + 016_schema_consistency.

Bestands-DBs (Schema aus der Migrationskette bzw. aus aelteren Models) muessen
nach dem Upgrade dieselben Indizes/Constraints haben wie eine Frisch-
Installation (create_all aus den aktuellen Models) — und der Tabellen-Rebuild
darf keine Kind-Zeilen per ON DELETE CASCADE vernichten.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_TARGET = "016_schema_consistency"

# Schema-Ausschnitt wie ihn die Migrationskette bis 013 hinterlaesst:
# Spalten-UNIQUE auf documents.file_hash (002), nullable priority (004),
# processing_jobs.email_account_id ohne FK (009).
_LEGACY_DDL = """
CREATE TABLE filing_scopes (
    id VARCHAR(36) PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, slug VARCHAR(100) NOT NULL UNIQUE,
    description TEXT, keywords TEXT, is_default BOOLEAN NOT NULL DEFAULT 0, color VARCHAR(7),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE documents (
    id VARCHAR(36) NOT NULL, original_filename VARCHAR(500) NOT NULL, stored_filename VARCHAR(500) NOT NULL,
    file_path VARCHAR(1000) NOT NULL, thumbnail_path VARCHAR(1000), file_type VARCHAR(10) NOT NULL,
    file_size_bytes INTEGER NOT NULL, file_hash VARCHAR(64) NOT NULL,
    document_type VARCHAR(50) DEFAULT 'SONSTIGES' NOT NULL, title VARCHAR(500) DEFAULT 'Unbekanntes Dokument' NOT NULL,
    document_date DATE, amount NUMERIC(12, 2), currency VARCHAR(3) DEFAULT 'EUR' NOT NULL,
    issuer VARCHAR(500), recipient VARCHAR(500), reference_number VARCHAR(200), summary TEXT,
    ocr_text TEXT DEFAULT '' NOT NULL, ocr_confidence FLOAT DEFAULT '0.0' NOT NULL,
    tax_relevant BOOLEAN DEFAULT '0' NOT NULL, tax_year INTEGER, tax_category VARCHAR(50),
    status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL, review_status VARCHAR(20) DEFAULT 'OK' NOT NULL,
    ai_confidence FLOAT DEFAULT '0.0' NOT NULL,
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    scanned_at DATETIME, filing_scope_id VARCHAR(36),
    PRIMARY KEY (id), UNIQUE (file_hash),
    CONSTRAINT fk_documents_filing_scope_id FOREIGN KEY(filing_scope_id) REFERENCES filing_scopes (id)
);
CREATE INDEX ix_documents_status ON documents (status);
CREATE INDEX ix_documents_filing_scope_id ON documents (filing_scope_id);
CREATE TABLE tags (
    id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, category VARCHAR(100),
    is_auto_generated BOOLEAN DEFAULT '1' NOT NULL, created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    PRIMARY KEY (id), UNIQUE (name)
);
CREATE TABLE document_tags (
    document_id VARCHAR(36) NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY (document_id, tag_id),
    FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE
);
CREATE TABLE review_questions (
    id VARCHAR(36) NOT NULL, document_id VARCHAR(36) NOT NULL, question TEXT NOT NULL, answer TEXT,
    field_affected VARCHAR(100), is_answered BOOLEAN DEFAULT '0' NOT NULL,
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, answered_at DATETIME,
    question_type VARCHAR(20), explanation TEXT, suggested_answers TEXT, priority INTEGER DEFAULT '0',
    PRIMARY KEY (id), FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);
CREATE INDEX ix_review_questions_document_id ON review_questions (document_id);
CREATE INDEX ix_review_questions_is_answered ON review_questions (is_answered);
CREATE TABLE notifications (
    id VARCHAR(36) NOT NULL, type VARCHAR(30) NOT NULL, title VARCHAR(500) NOT NULL, message TEXT NOT NULL,
    document_id VARCHAR(36), is_read BOOLEAN DEFAULT '0' NOT NULL,
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    PRIMARY KEY (id), FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE SET NULL
);
CREATE TABLE chat_messages (
    id VARCHAR(36) NOT NULL, role VARCHAR(20) NOT NULL, content TEXT NOT NULL, sources_json TEXT,
    filing_scope_id VARCHAR(36), created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, PRIMARY KEY (id)
);
CREATE TABLE email_accounts (id INTEGER NOT NULL, name VARCHAR(200) NOT NULL, PRIMARY KEY (id));
CREATE TABLE processing_jobs (
    id VARCHAR(36) NOT NULL, original_filename VARCHAR(500) NOT NULL, stored_filename VARCHAR(500) NOT NULL,
    file_path VARCHAR(1000) NOT NULL, file_type VARCHAR(10) NOT NULL, file_size_bytes INTEGER NOT NULL,
    source VARCHAR(12) NOT NULL, status VARCHAR(12) NOT NULL, error_message TEXT, retry_count INTEGER NOT NULL,
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    ocr_text TEXT, ocr_confidence FLOAT, analysis_result TEXT, email_account_id INTEGER,
    processing_started_at DATETIME, PRIMARY KEY (id)
);
CREATE INDEX ix_processing_jobs_status ON processing_jobs (status);
CREATE TABLE processed_emails (
    id INTEGER NOT NULL, email_account_id INTEGER NOT NULL, message_id VARCHAR(1000) NOT NULL,
    status VARCHAR(20) NOT NULL, processing_job_id VARCHAR(36), PRIMARY KEY (id),
    FOREIGN KEY(email_account_id) REFERENCES email_accounts (id) ON DELETE CASCADE,
    FOREIGN KEY(processing_job_id) REFERENCES processing_jobs (id) ON DELETE SET NULL
);
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, PRIMARY KEY (version_num));
INSERT INTO alembic_version VALUES ('013_fk_indexes_consistency');
"""

_DOC_COLUMNS = (
    "id, original_filename, stored_filename, file_path, file_type, file_size_bytes, file_hash, status"
)


def _env(db: Path) -> dict:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "PYTHONPATH": str(_BACKEND),
    }


def _alembic(db: Path, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_BACKEND), env=_env(db), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _create_all(db: Path) -> None:
    """Frisch-Installations-Pfad (migrate.create_fresh_schema: create_all aus den Models)."""
    result = subprocess.run(
        [sys.executable, "-c", "import migrate; migrate.create_fresh_schema()"],
        cwd=str(_BACKEND), env=_env(db), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _legacy_db(tmp_path: Path) -> Path:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(_LEGACY_DDL)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO email_accounts (id, name) VALUES (1, 'Praxis')")
        conn.execute(
            f"INSERT INTO documents ({_DOC_COLUMNS}) "
            "VALUES ('d1', 'a.pdf', 'x_a.pdf', '/a', 'pdf', 1, 'hash-a', 'DELETED')"
        )
        conn.execute("INSERT INTO tags (id, name) VALUES (1, 'strom')")
        conn.execute("INSERT INTO document_tags VALUES ('d1', 1)")
        conn.execute(
            "INSERT INTO review_questions (id, document_id, question, priority) VALUES ('q1', 'd1', '?', NULL)"
        )
        conn.execute("INSERT INTO notifications (id, type, title, message, document_id) VALUES ('n1', 'SYSTEM', 't', 'm', 'd1')")
        conn.execute(
            "INSERT INTO processing_jobs (id, original_filename, stored_filename, file_path, file_type, "
            "file_size_bytes, source, status, retry_count, email_account_id) VALUES "
            "('j1', 'a', 'a', '/a', 'pdf', 1, 'EMAIL', 'COMPLETED', 0, 1), "
            "('j2', 'b', 'b', '/b', 'pdf', 1, 'EMAIL', 'COMPLETED', 0, 999)"
        )
        conn.execute(
            "INSERT INTO processed_emails (id, email_account_id, message_id, status, processing_job_id) "
            "VALUES (1, 1, '<m1>', 'RELEVANT', 'j1')"
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _schema_facts(db: Path) -> dict:
    """Vergleichbare Schema-Eigenschaften der von den Migrationen betroffenen Stellen."""
    conn = sqlite3.connect(db)
    try:
        def indexes(table):
            out = {}
            for _, name, unique, origin, partial in conn.execute(f"PRAGMA index_list({table})"):
                cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info('{name}')"))
                out[cols] = (bool(unique), origin, bool(partial))
            return out

        def fks(table):
            return {(r[3], r[2], r[6]) for r in conn.execute(f"PRAGMA foreign_key_list({table})")}

        def notnull(table, column):
            return next(bool(r[3]) for r in conn.execute(f"PRAGMA table_info({table})") if r[1] == column)

        doc_idx = indexes("documents")
        return {
            "file_hash_index": doc_idx.get(("file_hash",)),
            "notifications_is_read": ("is_read",) in indexes("notifications"),
            "jobs_created_at": ("created_at",) in indexes("processing_jobs"),
            "jobs_document_id": ("document_id",) in indexes("processing_jobs"),
            "rq_is_answered": ("is_answered",) in indexes("review_questions"),
            "chat_created_at": ("created_at",) in indexes("chat_messages"),
            "jobs_fks": fks("processing_jobs"),
            "rq_priority_notnull": notnull("review_questions", "priority"),
        }
    finally:
        conn.close()


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    db = _legacy_db(tmp_path)
    _alembic(db, "upgrade", _TARGET)
    return db


def test_upgrade_keeps_child_rows(legacy_db: Path):
    """Der documents-Rebuild darf document_tags/review_questions nicht per CASCADE loeschen."""
    conn = sqlite3.connect(legacy_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM document_tags").fetchone()[0] == 1
        assert conn.execute("SELECT priority FROM review_questions").fetchall() == [(0,)]
        assert conn.execute("SELECT document_id FROM notifications").fetchall() == [("d1",)]
        assert conn.execute("SELECT processing_job_id FROM processed_emails").fetchall() == [("j1",)]
        jobs = dict(conn.execute("SELECT id, email_account_id FROM processing_jobs"))
        assert jobs == {"j1": 1, "j2": None}  # verwaister Verweis genullt
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_deleted_document_hash_can_be_reimported(legacy_db: Path):
    conn = sqlite3.connect(legacy_db)
    try:
        insert = (
            f"INSERT INTO documents ({_DOC_COLUMNS}) "
            "VALUES (?, 'a.pdf', 'y_a.pdf', '/a2', 'pdf', 1, 'hash-a', 'ACTIVE')"
        )
        conn.execute(insert, ("d2",))  # geloeschtes d1 blockiert nicht
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("d3",))  # zweites aktives schon
    finally:
        conn.close()


def test_upgrade_is_idempotent(legacy_db: Path):
    _alembic(legacy_db, "stamp", "013_fk_indexes_consistency")
    _alembic(legacy_db, "upgrade", _TARGET)


def test_migrated_schema_matches_fresh_install(legacy_db: Path, tmp_path: Path):
    fresh = tmp_path / "fresh.db"
    _create_all(fresh)

    migrated_facts = _schema_facts(legacy_db)
    assert migrated_facts == _schema_facts(fresh)
    assert migrated_facts["file_hash_index"] == (True, "c", True)  # Partial Unique Index
    assert ("email_account_id", "email_accounts", "SET NULL") in migrated_facts["jobs_fks"]
    assert ("document_id", "documents", "SET NULL") in migrated_facts["jobs_fks"]


def test_create_all_db_drops_global_hash_index(tmp_path: Path):
    """Frisch-Installationen vor 1.6 (create_all: Unique-Index ix_documents_file_hash)."""
    db = tmp_path / "old_fresh.db"
    _create_all(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP INDEX ux_documents_file_hash_active")
        conn.execute("CREATE UNIQUE INDEX ix_documents_file_hash ON documents (file_hash)")
        conn.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        conn.execute("DELETE FROM alembic_version")
        conn.execute("INSERT INTO alembic_version VALUES ('013_fk_indexes_consistency')")
        conn.commit()
    finally:
        conn.close()

    _alembic(db, "upgrade", _TARGET)

    conn = sqlite3.connect(db)
    try:
        names = {r[1] for r in conn.execute("PRAGMA index_list(documents)")}
    finally:
        conn.close()
    assert "ix_documents_file_hash" not in names
    assert "ux_documents_file_hash_active" in names


def test_detect_stamp_recognizes_new_revisions(legacy_db: Path):
    sys.path.insert(0, str(_BACKEND))
    import migrate

    conn = sqlite3.connect(legacy_db)
    try:
        cur = conn.cursor()
        assert migrate.detect_stamp(cur) == "016_schema_consistency"
        conn.execute("DROP INDEX ux_documents_file_hash_active")
        assert migrate.detect_stamp(cur) == "015_job_document_id"
    finally:
        conn.close()
