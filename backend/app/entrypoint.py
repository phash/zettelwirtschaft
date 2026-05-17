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
    """`migrate.py` ausfuehren — gleiche Logik wie im Docker-Entrypoint.

    Im PyInstaller-Bundle ist `alembic` nicht als CLI verfuegbar. Wir
    importieren die Public-API direkt.
    """
    # Lazy imports — sonst zieht entrypoint.py alembic schon beim --help mit.
    import sqlite3

    from app.config import get_settings

    settings = get_settings()

    # DATABASE_URL aufloesen
    db_url = settings.DATABASE_URL
    db_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 1. Legacy-Stamping (analog backend/migrate.py)
    from migrate import detect_stamp  # Top-level Modul im Bundle

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
            stamp = detect_stamp(cur)
            if stamp:
                logging.info("[migrate] Legacy-DB stamping auf %s", stamp)
                cur.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (stamp,))
                conn.commit()
        conn.commit()
    finally:
        conn.close()

    # 2. Alembic upgrade head via Public-API (keine subprocess-Calls)
    from alembic import command
    from alembic.config import Config as AlembicConfig

    # Bei PyInstaller liegt alembic.ini im _MEIPASS-Root
    if getattr(sys, "frozen", False):
        ini_path = Path(sys._MEIPASS) / "alembic.ini"
    else:
        ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"

    cfg = AlembicConfig(str(ini_path))
    # Alembic-Scripts-Pfad relativ aufloesen
    if getattr(sys, "frozen", False):
        cfg.set_main_option("script_location", str(Path(sys._MEIPASS) / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("+aiosqlite", ""))
    command.upgrade(cfg, "head")
    logging.info("[migrate] Alembic-Migrationen abgeschlossen")
    return 0


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
    logging.info("Starte Backend auf %s:%s", settings.SERVER_HOST, settings.SERVER_PORT)
    uvicorn.run(
        "app.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        # Native: kein Reload, kein Worker-Pool — wir laufen als Windows-Service
        # und PIN-Sessions sind in-memory (siehe N-06).
        reload=False,
        workers=1,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
