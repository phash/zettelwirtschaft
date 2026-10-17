#!/usr/bin/env python3
"""
Datenbankmigrationen vor dem App-Start.

Unterstützt Legacy-Installationen ohne alembic_version-Tracking:
- Erkennt anhand tatsächlicher Tabellen/Spalten welche Migrationen angewandt wurden
- Stempelt die alembic_version entsprechend
- Frische (leere) DBs: Schema via create_all + stamp head
- Führt dann `alembic upgrade head` aus
- Stellt sicher, dass der Default-Ablagebereich "Privat" existiert
"""
import os
import sqlite3
import subprocess
import sys
import uuid
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


def has_fk(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    """Prueft ob auf der Spalte ein FK-Constraint existiert (PRAGMA foreign_key_list)."""
    try:
        cur.execute(f"PRAGMA foreign_key_list({table})")
        for row in cur.fetchall():
            # row: (id, seq, table, from, to, on_update, on_delete, match)
            if row[3] == column:
                return True
    except sqlite3.OperationalError:
        return False
    return False


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
    # H-ARCH: 013 prüft an der **FK-Existenz** (nicht nur Index) — sonst stempelt
    # ein Legacy-Schema, dessen Index zufaellig via create_all entstanden ist,
    # auf 013 und die Migration legt den FK nie an (K-1 Re-Review).
    if has_fk(cur, "chat_messages", "filing_scope_id"):
        return "013_fk_indexes_consistency"
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
        return "004_notifications_corrections"
    if has_table(cur, "documents_fts"):
        return "003_add_fts5_saved"
    if has_table(cur, "documents"):
        return "002_add_document_models"
    if has_table(cur, "processing_jobs") and has_column(
        cur, "processing_jobs", "ocr_text"
    ):
        return "001_add_ocr_analysis"
    return None


def is_fresh_db(cur: sqlite3.Cursor) -> bool:
    """True wenn die DB noch keine User-Tabellen hat (alembic_version zaehlt nicht)."""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
    )
    return cur.fetchone() is None


def create_fresh_schema() -> None:
    """Legt das komplette Schema einer frischen DB via ``Base.metadata.create_all`` an.

    Die Alembic-Kette kann eine leere DB NICHT aufbauen: Migration 001 ist ein
    ``ALTER TABLE processing_jobs`` ohne vorheriges CREATE (historisch auf
    ``init_db()`` angewiesen). Frische DBs werden deshalb aus den Models erzeugt
    und danach vom Aufrufer auf head gestempelt — gemeinsamer Pfad fuer Docker
    (``main`` unten) und Native (``app/entrypoint.py``).

    Nutzt die App-Engine (DATABASE_URL aus den Settings) — im Docker-Betrieb
    dieselbe ENV-Variable, aus der ``DB_PATH`` oben abgeleitet ist.
    """
    import asyncio

    from app.database import Base, engine

    async def _create() -> None:
        # Alle Models registrieren — app.models/__init__ importiert die
        # E-Mail-Models nicht, daher explizit.
        import app.models  # noqa: F401
        from app.models import email_account, processed_email  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())


def ensure_default_filing_scope(cur: sqlite3.Cursor) -> bool:
    """Legt den Default-Ablagebereich "Privat" an, falls noch gar keiner existiert.

    Migration 005 seedet ihn — bei frischen DBs (create_all + stamp head) laeuft
    005 aber nie. Idempotent und auch fuer bereits so aufgesetzte Installationen
    wirksam (der Default-Scope ist per API nicht loeschbar, eine leere Tabelle
    ist also immer ein Bootstrap-Fehler). Werte identisch zu Migration 005.
    Returns True wenn angelegt.
    """
    if not has_table(cur, "filing_scopes"):
        return False
    cur.execute("SELECT 1 FROM filing_scopes LIMIT 1")
    if cur.fetchone() is not None:
        return False
    cur.execute(
        "INSERT INTO filing_scopes (id, name, slug, description, keywords, is_default, color, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (str(uuid.uuid4()), "Privat", "privat", "Privater Haushalt", "[]", 1, "#3B82F6"),
    )
    return True


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

    conn.close()


def run_alembic(*args: str) -> None:
    # M6: cwd aus dem Skript-Pfad ableiten (alembic.ini liegt neben migrate.py)
    # statt "/app" hartzucodieren — laeuft so auch ausserhalb des Docker-Image.
    result = subprocess.run(
        # sys.executable -m alembic statt "alembic" im PATH: nutzt garantiert
        # dasselbe Environment wie dieses Skript (venv, Docker-Prefix).
        [sys.executable, "-m", "alembic", *args],
        cwd=str(Path(__file__).resolve().parent),
    )
    if result.returncode != 0:
        print(f"[migrate] FEHLER: alembic {' '.join(args)} fehlgeschlagen", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        fresh = is_fresh_db(conn.cursor())
    finally:
        conn.close()

    if fresh:
        # Frische DB: Schema aus den Models + Stamp auf head (die Alembic-Kette
        # kann eine leere DB nicht aufbauen, siehe create_fresh_schema). Gleicher
        # Pfad wie Native (app/entrypoint.py). B-01 bleibt gewahrt: create_all
        # laeuft NUR auf leeren DBs, Legacy-DBs gehen ueber detect_stamp().
        print("[migrate] Frische DB – Schema via create_all, stamp auf head", flush=True)
        create_fresh_schema()
        run_alembic("stamp", "head")
    else:
        fix_alembic_version()

    print("[migrate] Fuehre alembic upgrade head aus...", flush=True)
    run_alembic("upgrade", "head")

    conn = sqlite3.connect(DB_PATH)
    try:
        if ensure_default_filing_scope(conn.cursor()):
            conn.commit()
            print("[migrate] Default-Ablagebereich 'Privat' angelegt", flush=True)
    finally:
        conn.close()
    print("[migrate] Migrationen abgeschlossen", flush=True)


if __name__ == "__main__":
    main()
