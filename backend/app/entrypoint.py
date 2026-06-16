"""Native-Windows-Entrypoint fuer das PyInstaller-Bundle.

Aufrufpfad:
    zettelwirtschaft-backend.exe [--config <path>] [--migrate-only]

- Liest ZETTELWIRTSCHAFT_CONFIG aus ENV oder --config-Argument.
- Stellt sicher, dass die Datenverzeichnisse existieren.
- Fuehrt Alembic-Migrationen aus (idempotent).
- Startet uvicorn programmatisch mit SERVER_HOST/SERVER_PORT aus Settings.

Im Docker-Betrieb wird stattdessen `entrypoint.sh` mit `uvicorn` direkt
genutzt. Diese Datei ist der reine Native-Pfad.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _set_config_from_args(args: argparse.Namespace) -> None:
    if args.config:
        os.environ["ZETTELWIRTSCHAFT_CONFIG"] = str(Path(args.config).resolve())


def _ensure_data_dirs() -> None:
    """Verzeichnisse anlegen bevor die App startet — sonst wirft die DB-Init."""
    # Lazy import damit ENV-Vars bereits gesetzt sind
    from app.config import get_settings

    settings = get_settings()
    for d in (settings.UPLOAD_DIR, settings.WATCH_DIR, settings.ARCHIVE_DIR, settings.THUMBNAIL_DIR):
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)
    if settings.EXPORT_DIR:
        Path(settings.EXPORT_DIR).mkdir(parents=True, exist_ok=True)

    if settings.CHROMADB_MODE == "embedded":
        from app.services.vectorize_service import _resolve_chroma_path
        _resolve_chroma_path(settings).mkdir(parents=True, exist_ok=True)


def _run_migrations() -> int:
    """Migrationen ausfuehren.

    Drei Faelle:
    1. Frische DB (kein Schema): Base.metadata.create_all() + stamp auf head.
       (Migration 001 macht ALTER ohne vorheriges CREATE — sie geht historisch
       davon aus dass SQLAlchemy create_all schon gelaufen ist.)
    2. Legacy-DB ohne alembic_version: detect_stamp + upgrade.
    3. Normale DB mit alembic_version: upgrade head (no-op wenn aktuell).
    """
    # Lazy imports — sonst zieht entrypoint.py alembic schon beim --help mit.
    import sqlite3

    from app.config import get_settings

    settings = get_settings()

    db_url = settings.DATABASE_URL
    db_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 1. Pruefen ob die DB komplett leer ist (keine User-Tabellen)
    is_fresh_db = False
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        )
        existing_tables = [r[0] for r in cur.fetchall()]
        is_fresh_db = len(existing_tables) == 0
    finally:
        conn.close()

    if is_fresh_db:
        # Frische DB: Schema komplett ueber SQLAlchemy create_all anlegen,
        # dann auf head stampen. Migrationen sind dann no-op.
        import asyncio
        from app.database import Base, engine

        async def _create_schema():
            # Alle Models importieren damit Base.metadata komplett ist
            import app.models  # noqa: F401
            from app.models import (  # noqa: F401
                audit_log, chat_message, correction_mapping, document,
                email_account, filing_scope, notification, processed_email,
                processing_job, review_question, saved_search, warranty_info,
            )
            async with engine.begin() as conn_:
                await conn_.run_sync(Base.metadata.create_all)
            await engine.dispose()

        logging.info("[migrate] Frische DB — Schema via create_all anlegen")
        asyncio.run(_create_schema())

    # 2. Legacy-Stamping (DBs ohne alembic_version)
    from migrate import detect_stamp

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
        cur.execute("SELECT version_num FROM alembic_version")
        versions = [r[0] for r in cur.fetchall()]
        if not versions:
            if is_fresh_db:
                # Eben angelegtes Schema entspricht head — direkt stampen.
                # Holen wir den head dynamisch.
                from alembic.script import ScriptDirectory
                from alembic.config import Config as AlembicConfig
                ini_path = _resolve_alembic_ini()
                _cfg = AlembicConfig(str(ini_path))
                if getattr(sys, "frozen", False):
                    _cfg.set_main_option("script_location", str(Path(sys._MEIPASS) / "alembic"))
                head_rev = ScriptDirectory.from_config(_cfg).get_current_head()
                logging.info("[migrate] Frisches Schema stamping auf %s", head_rev)
                cur.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (head_rev,))
            else:
                stamp = detect_stamp(cur)
                if stamp:
                    logging.info("[migrate] Legacy-DB stamping auf %s", stamp)
                    cur.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (stamp,))
        conn.commit()
    finally:
        conn.close()

    # 3. Alembic upgrade head via Public-API (keine subprocess-Calls)
    from alembic import command
    from alembic.config import Config as AlembicConfig

    ini_path = _resolve_alembic_ini()
    cfg = AlembicConfig(str(ini_path))
    if getattr(sys, "frozen", False):
        cfg.set_main_option("script_location", str(Path(sys._MEIPASS) / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("+aiosqlite", ""))
    command.upgrade(cfg, "head")
    logging.info("[migrate] Alembic-Migrationen abgeschlossen")
    return 0


def _resolve_alembic_ini() -> Path:
    """alembic.ini-Pfad — im Bundle in _MEIPASS, sonst im Dev-backend/."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "alembic.ini"
    return Path(__file__).resolve().parents[1] / "alembic.ini"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zettelwirtschaft-backend",
        description="Zettelwirtschaft Backend (Native-Windows)",
    )
    parser.add_argument("--config", help="Pfad zur config.toml (alternativ: ENV ZETTELWIRTSCHAFT_CONFIG)")
    parser.add_argument("--migrate-only", action="store_true", help="Nur Migrationen ausfuehren, dann beenden")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    _set_config_from_args(args)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.version:
        from app.main import _read_version
        print(_read_version())
        return 0

    _ensure_data_dirs()
    _run_migrations()

    if args.migrate_only:
        return 0

    # Programmatisch uvicorn starten — kein externes uvicorn CLI noetig.
    import uvicorn

    from app.config import get_settings

    settings = get_settings()

    # Optionales TLS (Native-HTTPS) — nur wenn beide Pfade gesetzt sind und
    # existieren. Ermoeglicht Smartphone-Scan + PWA im LAN (Secure Context).
    ssl_kwargs: dict = {}
    cert, key = settings.SERVER_SSL_CERTFILE, settings.SERVER_SSL_KEYFILE
    if cert and key:
        if Path(cert).exists() and Path(key).exists():
            ssl_kwargs = {"ssl_certfile": cert, "ssl_keyfile": key}
            logging.info("TLS aktiv (Cert: %s)", cert)
        else:
            logging.warning(
                "SERVER_SSL_CERTFILE/KEYFILE gesetzt, aber Datei fehlt — starte ohne TLS (HTTP)."
            )

    scheme = "https" if ssl_kwargs else "http"
    logging.info("Starte Backend auf %s://%s:%s", scheme, settings.SERVER_HOST, settings.SERVER_PORT)
    uvicorn.run(
        "app.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        # Native: kein Reload, kein Worker-Pool — wir laufen als Windows-Service
        # und PIN-Sessions sind in-memory (siehe N-06).
        reload=False,
        workers=1,
        **ssl_kwargs,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
