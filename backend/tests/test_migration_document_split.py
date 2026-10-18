"""Migration 018_document_split (Spezifikation 12).

Bestands-DB (Schema der Migrationskette) -> head: gleiche Spalten, FKs und
partielle Unique-Indizes wie eine Frisch-Installation (create_all), neue
Enum-Werte lesbar, Downgrade entfernt alles wieder.
"""

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_migration_ingest_schema import _BACKEND, _alembic, _create_all, _legacy_db

_REVISION = "018_document_split"


def _facts(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        def columns(table):
            return {r[1]: (r[2].upper(), bool(r[3])) for r in conn.execute(f"PRAGMA table_info({table})")}

        def indexes(table):
            out = {}
            for _, name, unique, _origin, partial in conn.execute(f"PRAGMA index_list({table})"):
                cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info('{name}')"))
                out[cols] = (bool(unique), bool(partial))
            return out

        def fks(table):
            return {(r[3], r[2], r[6]) for r in conn.execute(f"PRAGMA foreign_key_list({table})")}

        job_cols = columns("processing_jobs")
        doc_cols = columns("documents")
        return {
            "batch_table": bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='split_batches'"
            ).fetchone()),
            "batch_columns": set(columns("split_batches")),
            "job_split_columns": {c: job_cols.get(c) for c in (
                "split_batch_id", "split_role", "split_part_index", "page_from",
                "page_to", "split_mode", "page_ocr")},
            "doc_split_columns": {c: doc_cols.get(c) for c in (
                "split_batch_id", "split_part_index", "source_pages")},
            "job_indexes": {k: v for k, v in indexes("processing_jobs").items() if "split_batch_id" in k},
            "doc_indexes": {
                k: v for k, v in indexes("documents").items()
                if "split_batch_id" in k or k == ("file_hash",)
            },
            "batch_indexes": indexes("split_batches"),
            "job_fk": ("split_batch_id", "split_batches", "SET NULL") in fks("processing_jobs"),
            "doc_fk": ("split_batch_id", "split_batches", "SET NULL") in fks("documents"),
            "batch_fks": fks("split_batches"),
        }
    finally:
        conn.close()


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db = _legacy_db(tmp_path)
    _alembic(db, "upgrade", "head")
    return db


def test_migrated_schema_matches_fresh_install(migrated_db: Path, tmp_path: Path):
    fresh = tmp_path / "fresh.db"
    _create_all(fresh)

    migrated = _facts(migrated_db)
    assert migrated == _facts(fresh)
    assert migrated["job_fk"] and migrated["doc_fk"]
    assert migrated["batch_fks"] == set()  # bewusst kein FK auf Jobs/Dokumente (kein Zyklus)
    assert migrated["job_indexes"][("split_batch_id", "split_part_index")] == (True, True)
    assert migrated["doc_indexes"][("split_batch_id", "split_part_index")] == (True, True)
    assert migrated["doc_indexes"][("file_hash",)] == (True, True)  # Partial Index aus 016 bleibt
    assert migrated["job_split_columns"]["split_mode"] == ("VARCHAR(10)", True)


def test_upgrade_keeps_rows_and_defaults_split_mode(migrated_db: Path):
    conn = sqlite3.connect(migrated_db)
    try:
        jobs = dict(conn.execute("SELECT id, split_mode FROM processing_jobs"))
        assert jobs == {"j1": "AUTO", "j2": "AUTO"}
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM document_tags").fetchone()[0] == 1
        assert conn.execute("SELECT processing_job_id FROM processed_emails").fetchall() == [("j1",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_new_enum_values_are_readable(migrated_db: Path):
    """Bestands-Spalten sind VARCHAR(12): AWAITING_SPLIT (14) passt trotzdem."""
    from app.models import JobSource, JobStatus, ProcessingJob

    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "INSERT INTO processing_jobs (id, original_filename, stored_filename, file_path, file_type, "
            "file_size_bytes, source, status, retry_count) VALUES "
            "('j3', 'stapel.pdf', 's', '/s', 'pdf', 1, 'SPLIT_REQUEST', 'AWAITING_SPLIT', 0)"
        )
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{migrated_db.as_posix()}")
    try:
        with Session(engine) as session:
            job = session.execute(select(ProcessingJob).where(ProcessingJob.id == "j3")).scalar_one()
            assert job.status == JobStatus.AWAITING_SPLIT
            assert job.source == JobSource.SPLIT_REQUEST
            assert job.split_mode == "AUTO"
    finally:
        engine.dispose()


def test_upgrade_is_idempotent(migrated_db: Path):
    _alembic(migrated_db, "stamp", "017_review_data_fixes")
    _alembic(migrated_db, "upgrade", "head")


def test_downgrade_removes_split_schema(migrated_db: Path):
    _alembic(migrated_db, "downgrade", "017_review_data_fixes")
    facts = _facts(migrated_db)
    assert not facts["batch_table"]
    assert all(v is None for v in facts["job_split_columns"].values())
    assert all(v is None for v in facts["doc_split_columns"].values())
    assert facts["doc_indexes"][("file_hash",)] == (True, True)
    conn = sqlite3.connect(migrated_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()[0] == 2
    finally:
        conn.close()


def test_detect_stamp_recognizes_split_batches(migrated_db: Path):
    sys.path.insert(0, str(_BACKEND))
    import migrate

    conn = sqlite3.connect(migrated_db)
    try:
        cur = conn.cursor()
        assert migrate.detect_stamp(cur) == _REVISION
        conn.execute("DROP TABLE split_batches")
        assert migrate.detect_stamp(cur) == "016_schema_consistency"
    finally:
        conn.close()
