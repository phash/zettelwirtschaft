"""Migration 019_reanalysis (Issue #39): Tabellen, Spalte, Stamp, Single-Active-Run-Index."""

import sqlite3
import sys
from pathlib import Path

import pytest

from tests.test_migration_ingest_schema import _BACKEND, _alembic, _create_all, _legacy_db

_TABLES = ("reanalysis_proposals", "reanalysis_runs", "reanalysis_run_items")


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db = _legacy_db(tmp_path)
    _alembic(db, "upgrade", "head")
    return db


def test_upgrade_from_018_adds_tables_and_column(tmp_path: Path):
    db = _legacy_db(tmp_path)
    _alembic(db, "upgrade", "018_document_split")
    conn = sqlite3.connect(db)
    try:
        assert not set(_TABLES) & _tables(conn)
        assert "analysis_model" not in _columns(conn, "documents")
    finally:
        conn.close()

    _alembic(db, "upgrade", "head")
    conn = sqlite3.connect(db)
    try:
        assert set(_TABLES) <= _tables(conn)
        assert "analysis_model" in _columns(conn, "documents")
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert conn.execute("SELECT analysis_model FROM documents").fetchall() == [(None,)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_upgrade_is_idempotent_on_fresh_schema(tmp_path: Path):
    db = tmp_path / "fresh.db"
    _create_all(db)
    _alembic(db, "stamp", "018_document_split")
    _alembic(db, "upgrade", "head")


def _facts(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        out = {
            t: {r[1]: (r[2].upper(), bool(r[3])) for r in conn.execute(f"PRAGMA table_info({t})")}
            for t in _TABLES
        }
        out["doc_analysis_model"] = {
            r[1]: (r[2].upper(), bool(r[3])) for r in conn.execute("PRAGMA table_info(documents)")
        }["analysis_model"]
        out["indexes"] = {
            name: (bool(unique), bool(partial))
            for t in _TABLES
            for _, name, unique, _origin, partial in conn.execute(f"PRAGMA index_list({t})")
            if name.startswith(("ux_reanalysis", "ix_reanalysis"))
        }
        out["fks"] = {
            t: {(r[3], r[2], r[6]) for r in conn.execute(f"PRAGMA foreign_key_list({t})")}
            for t in _TABLES
        }
        return out
    finally:
        conn.close()


def test_migrated_schema_matches_fresh_install(migrated_db: Path, tmp_path: Path):
    fresh = tmp_path / "fresh.db"
    _create_all(fresh)
    assert _facts(migrated_db) == _facts(fresh)


def test_detect_stamp_recognizes_019(migrated_db: Path):
    sys.path.insert(0, str(_BACKEND))
    import migrate

    conn = sqlite3.connect(migrated_db)
    try:
        cur = conn.cursor()
        assert migrate.detect_stamp(cur) == "019_reanalysis"
        for table in reversed(_TABLES):
            conn.execute(f"DROP TABLE {table}")
        assert migrate.detect_stamp(cur) == "018_document_split"
    finally:
        conn.close()


def _insert_run(conn, run_id: str, status: str) -> None:
    conn.execute(
        "INSERT INTO reanalysis_runs (id, status, model, with_ocr, total, created_at) "
        "VALUES (?, ?, 'm', 0, 1, '2026-10-24 10:00:00')",
        (run_id, status),
    )


@pytest.mark.parametrize("db_kind", ["migrated", "fresh"])
def test_second_active_run_violates_unique_index(db_kind: str, migrated_db: Path, tmp_path: Path):
    db = migrated_db
    if db_kind == "fresh":
        db = tmp_path / "fresh.db"
        _create_all(db)
    conn = sqlite3.connect(db)
    try:
        _insert_run(conn, "r1", "RUNNING")
        _insert_run(conn, "r2", "DONE")  # abgeschlossene Laeufe stoeren nicht
        _insert_run(conn, "r3", "CANCELLED")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(conn, "r4", "WAITING_AI")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(conn, "r5", "PAUSED")
    finally:
        conn.close()


def test_downgrade_removes_reanalysis_schema(migrated_db: Path):
    _alembic(migrated_db, "downgrade", "018_document_split")
    conn = sqlite3.connect(migrated_db)
    try:
        assert not set(_TABLES) & _tables(conn)
        assert "analysis_model" not in _columns(conn, "documents")
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    finally:
        conn.close()
