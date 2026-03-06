#!/usr/bin/env python3
"""
Datenbankmigrationen vor dem App-Start.

Unterstützt Legacy-Installationen ohne alembic_version-Tracking:
- Erkennt anhand tatsächlicher Tabellen/Spalten welche Migrationen angewandt wurden
- Stempelt die alembic_version entsprechend
- Führt dann `alembic upgrade head` aus
"""
import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "sqlite+aiosqlite:///./data/zettelwirtschaft.db"
)
DB_PATH = DATABASE_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")


def has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def detect_stamp(cur: sqlite3.Cursor) -> str | None:
    """Erkennt die höchste angewandte Migration anhand des tatsächlichen Schemas."""
    if has_table(cur, "email_accounts"):
        return "009_add_email_accounts"
    if has_table(cur, "warranty_info") and has_column(
        cur, "warranty_info", "reminder_90d_sent"
    ):
        return "008_add_warranty_reminder_flags"
    if has_table(cur, "system_settings"):
        return "007_add_system_settings"
    if has_table(cur, "chat_messages"):
        return "006_add_chat_messages"
    if has_table(cur, "filing_scopes"):
        return "005_add_filing_scopes"
    if has_table(cur, "notifications"):
        return "004_add_notifications_corrections_review_ext"
    if has_table(cur, "documents_fts"):
        return "003_add_fts5_and_saved_searches"
    if has_table(cur, "documents"):
        return "002_add_document_models"
    if has_table(cur, "processing_jobs") and has_column(
        cur, "processing_jobs", "ocr_text"
    ):
        return "001_add_ocr_analysis_columns"
    return None


async def create_schema() -> None:
    """Erstellt initiales Schema via create_all (idempotent, für Frisch-Installs)."""
    import app.models.audit_log  # noqa: F401
    import app.models.chat_message  # noqa: F401
    import app.models.correction_mapping  # noqa: F401
    import app.models.document  # noqa: F401
    import app.models.email_account  # noqa: F401
    import app.models.filing_scope  # noqa: F401
    import app.models.notification  # noqa: F401
    import app.models.processed_email  # noqa: F401
    import app.models.processing_job  # noqa: F401
    import app.models.review_question  # noqa: F401
    import app.models.saved_search  # noqa: F401
    import app.models.warranty_info  # noqa: F401

    from app.database import init_db

    await init_db()


def fix_alembic_version() -> None:
    """Stempelt Legacy-DBs ohne alembic_version-Tracking auf die korrekte Version."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS alembic_version "
        "(version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    conn.commit()

    cur.execute("SELECT version_num FROM alembic_version")
    versions = [r[0] for r in cur.fetchall()]

    if not versions:
        stamp = detect_stamp(cur)
        if stamp:
            print(f"[migrate] Legacy-DB erkannt – stempel auf {stamp}", flush=True)
            cur.execute(
                "INSERT INTO alembic_version (version_num) VALUES (?)", (stamp,)
            )
            conn.commit()
        else:
            print(
                "[migrate] Frische DB – alembic erstellt vollständiges Schema",
                flush=True,
            )

    conn.close()


def run_alembic() -> None:
    result = subprocess.run(["alembic", "upgrade", "head"], cwd="/app")
    if result.returncode != 0:
        print("[migrate] FEHLER: alembic upgrade head fehlgeschlagen", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    print("[migrate] Erstelle/prüfe Datenbankschema...", flush=True)
    asyncio.run(create_schema())

    fix_alembic_version()

    print("[migrate] Führe alembic upgrade head aus...", flush=True)
    run_alembic()
    print("[migrate] Migrationen abgeschlossen", flush=True)


if __name__ == "__main__":
    main()
