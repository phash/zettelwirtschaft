#!/usr/bin/env python3
"""
Datenbankmigrationen vor dem App-Start.

Unterstützt Legacy-Installationen ohne alembic_version-Tracking:
- Erkennt anhand tatsächlicher Tabellen/Spalten welche Migrationen angewandt wurden
- Stempelt die alembic_version entsprechend
- Führt dann `alembic upgrade head` aus
"""
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


def has_index(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def column_type(cur: sqlite3.Cursor, table: str, column: str) -> str | None:
    cur.execute(f"PRAGMA table_info({table})")
    for row in cur.fetchall():
        if row[1] == column:
            return (row[2] or "").upper()
    return None


def detect_stamp(cur: sqlite3.Cursor) -> str | None:
    """Erkennt die höchste angewandte Migration anhand des tatsächlichen Schemas.

    Reihenfolge: neueste zuerst. B-01 (CODE_REVIEW_v3): bisher fehlten 010 + 011,
    sodass Frisch-Installs (DB von SQLAlchemy create_all aufgesetzt, alle Tabellen
    + Indizes da) auf 009 stempelten und 010+011 sich Doppelausführungen einhandelten.
    """
    # B5: 012 prüft Heartbeat-Spalte auf processing_jobs.
    if has_column(cur, "processing_jobs", "processing_started_at"):
        return "012_add_processing_started_at"
    if has_index(cur, "ix_notifications_is_read"):
        return "011_add_performance_indexes"
    # Migration 010 fixt email_accounts.filing_scope_id von Integer auf VARCHAR(36)
    if has_table(cur, "email_accounts"):
        col_type = column_type(cur, "email_accounts", "filing_scope_id")
        if col_type and ("VARCHAR" in col_type or "CHAR" in col_type or "TEXT" in col_type):
            return "010_fix_email_filing_scope_fk_type"
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

    # B-01: KEIN create_all mehr vor alembic. Frisch-Installs werden komplett
    # ueber die Migrationskette aufgebaut, Legacy-DBs ohne alembic_version
    # werden ueber detect_stamp() korrekt verortet (inkl. 010 + 011).
    fix_alembic_version()

    print("[migrate] Fuehre alembic upgrade head aus...", flush=True)
    run_alembic()
    print("[migrate] Migrationen abgeschlossen", flush=True)


if __name__ == "__main__":
    main()
