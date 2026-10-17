"""Migration fuer background_tasks: Upgrade von 013 und Fresh-DB-Pfad (create_all)."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]


def _env(db: Path) -> dict:
    return {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "PYTHONPATH": str(_BACKEND),
    }


def _run(args: list[str], db: Path) -> None:
    result = subprocess.run(
        [sys.executable, *args], cwd=str(_BACKEND), env=_env(db), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _schema(cur: sqlite3.Cursor) -> tuple[set[str], dict[str, str]]:
    cols = {row[1] for row in cur.execute("PRAGMA table_info(background_tasks)")}
    indexes = {
        name: sql or ""
        for name, sql in cur.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='background_tasks'"
        )
    }
    return cols, indexes


_EXPECTED_COLS = {
    "id", "kind", "key", "status", "progress_percent", "progress_text",
    "result", "error", "created_at", "started_at", "finished_at",
}


def test_fresh_db_contains_background_tasks(tmp_path):
    db = tmp_path / "fresh.db"
    _run([str(_BACKEND / "migrate.py")], db)

    conn = sqlite3.connect(db)
    try:
        cols, indexes = _schema(conn.cursor())
    finally:
        conn.close()
    assert cols == _EXPECTED_COLS
    assert "ux_background_tasks_active_kind_key" in indexes
    assert "WHERE" in indexes["ux_background_tasks_active_kind_key"].upper()


def test_upgrade_from_013_creates_table_idempotently(tmp_path):
    db = tmp_path / "legacy.db"
    _run([str(_BACKEND / "migrate.py")], db)

    # Bestands-DB auf Stand 013 simulieren: Tabelle weg, Version zurueck.
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE background_tasks")
        conn.execute("UPDATE alembic_version SET version_num = '013_fk_indexes_consistency'")
        conn.commit()
    finally:
        conn.close()

    _run(["-m", "alembic", "upgrade", "head"], db)
    _run(["-m", "alembic", "upgrade", "head"], db)  # zweiter Lauf: no-op

    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cols, indexes = _schema(cur)
        assert cols == _EXPECTED_COLS
        assert {"ix_background_tasks_kind", "ix_background_tasks_status",
                "ux_background_tasks_active_kind_key"} <= set(indexes)

        # Partial Unique Index greift nur fuer aktive Eintraege.
        insert = (
            "INSERT INTO background_tasks (id, kind, key, status, created_at) "
            "VALUES (?, 'backup', 'global', ?, CURRENT_TIMESTAMP)"
        )
        cur.execute(insert, ("t1", "RUNNING"))
        cur.execute(insert, ("t2", "SUCCEEDED"))
        try:
            cur.execute(insert, ("t3", "PENDING"))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("zweiter aktiver Task mit gleichem (kind, key) wurde akzeptiert")
    finally:
        conn.close()
