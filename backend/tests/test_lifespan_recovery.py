"""App-Start: durch einen Neustart unterbrochene Jobs und Hintergrund-Vorgaenge
werden im lifespan zurueckgesetzt (nicht erst nach einer Wartezeit).

Laeuft im Subprozess gegen eine tmp-DB: der lifespan nutzt die modulweite
App-Engine aus den Settings, die im Test-Prozess auf die Dev-DB zeigen wuerde.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]

_RUN_LIFESPAN = """
import asyncio
from app.main import app
from app.services.queue_worker_service import pause_queue

pause_queue()  # Worker soll den zurueckgesetzten Job nicht sofort neu aufgreifen

async def main():
    async with app.router.lifespan_context(app):
        pass

asyncio.run(main())
"""


def _env(tmp_path: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "ZETTELWIRTSCHAFT_CONFIG"}
    data = tmp_path / "data"
    env.update({
        "PYTHONPATH": str(_BACKEND),
        "DATABASE_URL": f"sqlite+aiosqlite:///{(data / 'zw.db').as_posix()}",
        "UPLOAD_DIR": str(data / "uploads"),
        "WATCH_DIR": str(data / "watch"),
        "ARCHIVE_DIR": str(data / "archive"),
        "THUMBNAIL_DIR": str(data / "thumbnails"),
        # Nie echte Dienste: Ollama/Chroma auf Port 9 (sofort "connection refused").
        "OLLAMA_BASE_URL": "http://127.0.0.1:9",
        "CHROMADB_HOST": "127.0.0.1",
        "CHROMADB_PORT": "9",
        "PIN_ENABLED": "false",
    })
    return env


def _run(args: list[str], env: dict) -> None:
    result = subprocess.run(
        [sys.executable, *args], cwd=str(_BACKEND), env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_startup_resets_interrupted_jobs_and_tasks(tmp_path):
    env = _env(tmp_path)
    _run([str(_BACKEND / "migrate.py")], env)
    db = tmp_path / "data" / "zw.db"

    conn = sqlite3.connect(db)
    try:
        # Gerade erst geclaimt (Heartbeat jetzt) — frueher blieb so ein Job fuer
        # immer PROCESSING, weil nur Heartbeats aelter als 10 Minuten zaehlten.
        conn.execute(
            "INSERT INTO processing_jobs (id, original_filename, stored_filename, file_path, file_type, "
            "file_size_bytes, source, status, retry_count, created_at, updated_at, processing_started_at) "
            "VALUES ('job-1', 'a.pdf', 'x_a.pdf', '/nirgends/x_a.pdf', 'pdf', 1, 'UPLOAD', 'PROCESSING', 0, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO background_tasks (id, kind, key, status, created_at) "
            "VALUES ('task-1', 'backup', 'global', 'RUNNING', CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()

    _run(["-c", _RUN_LIFESPAN], env)

    conn = sqlite3.connect(db)
    try:
        status, retries, started = conn.execute(
            "SELECT status, retry_count, processing_started_at FROM processing_jobs WHERE id = 'job-1'"
        ).fetchone()
        task_status, task_error = conn.execute(
            "SELECT status, error FROM background_tasks WHERE id = 'task-1'"
        ).fetchone()
    finally:
        conn.close()
    assert (status, retries, started) == ("PENDING", 1, None)
    assert task_status == "FAILED"
    assert "Neustart" in task_error


def test_startup_requeues_interrupted_reanalysis_items(tmp_path):
    """Massenlauf (Issue #39): ein beim Neustart laufendes Dokument kommt zurueck in die Warteschlange."""
    env = _env(tmp_path)
    _run([str(_BACKEND / "migrate.py")], env)
    db = tmp_path / "data" / "zw.db"

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO reanalysis_runs (id, status, model, with_ocr, total, created_at) "
            "VALUES ('run-1', 'RUNNING', 'gemma-test', 0, 2, CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO reanalysis_run_items (run_id, document_id, position, status) VALUES "
            "('run-1', 'doc-1', 0, 'PROCESSING'), ('run-1', 'doc-2', 1, 'PENDING')"
        )
        conn.commit()
    finally:
        conn.close()

    # Queue pausiert (_RUN_LIFESPAN): auch der Neu-Analyse-Runner greift nichts auf.
    _run(["-c", _RUN_LIFESPAN], env)

    conn = sqlite3.connect(db)
    try:
        items = conn.execute(
            "SELECT document_id, status FROM reanalysis_run_items ORDER BY position"
        ).fetchall()
        run_status, total = conn.execute(
            "SELECT status, total FROM reanalysis_runs WHERE id = 'run-1'"
        ).fetchone()
    finally:
        conn.close()
    assert items == [("doc-1", "PENDING"), ("doc-2", "PENDING")]
    assert (run_status, total) == ("RUNNING", 2)
