"""System-Health und Backup-API."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.document import Document, DocumentStatus
from app.services.backup_service import create_backup, get_system_info, list_backups
from app.services.settings_service import get_db_setting, set_db_setting

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


class FolderSettings(BaseModel):
    watch_dir: str
    export_dir: str
    watch_dir_host: str = ""
    export_dir_host: str = ""
    restart_required: bool = False


HOST_MOUNTS_FILE = "/app/data/.host-mounts.json"


def _write_host_mounts(watch_host: str, export_host: str) -> None:
    """Schreibt Host-Mount-Konfiguration nach data/.host-mounts.json."""
    from pathlib import Path
    mounts: dict = {}
    if watch_host:
        mounts["watch"] = watch_host
    if export_host:
        mounts["export"] = export_host
    path = Path(HOST_MOUNTS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mounts:
        path.write_text(json.dumps(mounts, indent=2), encoding="utf-8")
    elif path.exists():
        path.unlink()


@router.get("/system/settings", response_model=FolderSettings)
async def get_folder_settings(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Liest die konfigurierten Ordner-Einstellungen."""
    return FolderSettings(
        watch_dir=await get_db_setting(session, "watch_dir", settings.WATCH_DIR),
        export_dir=await get_db_setting(session, "export_dir", settings.EXPORT_DIR),
        watch_dir_host=await get_db_setting(session, "watch_dir_host", ""),
        export_dir_host=await get_db_setting(session, "export_dir_host", ""),
    )


@router.put("/system/settings", response_model=FolderSettings)
async def update_folder_settings(
    body: FolderSettings,
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Aktualisiert Watch-Ordner und Export-Ordner. Startet Watch-Task bei Pfadaenderung neu."""
    old_watch_dir = await get_db_setting(session, "watch_dir", settings.WATCH_DIR)

    restart_required = False
    watch_dir = body.watch_dir
    export_dir = body.export_dir

    # Host-Pfade: Container-Pfade automatisch setzen
    if body.watch_dir_host:
        watch_dir = "/app/external/watch"
    elif await get_db_setting(session, "watch_dir_host", ""):
        # Host-Pfad wurde geleert -> Standard zurueck
        watch_dir = watch_dir if watch_dir != "/app/external/watch" else settings.WATCH_DIR

    if body.export_dir_host:
        export_dir = "/app/external/export"
    elif await get_db_setting(session, "export_dir_host", ""):
        # Host-Pfad wurde geleert -> Standard zurueck
        export_dir = export_dir if export_dir != "/app/external/export" else ""

    # Pruefen ob Host-Mounts sich geaendert haben -> Neustart noetig
    old_watch_host = await get_db_setting(session, "watch_dir_host", "")
    old_export_host = await get_db_setting(session, "export_dir_host", "")
    if body.watch_dir_host != old_watch_host or body.export_dir_host != old_export_host:
        restart_required = True

    await set_db_setting(session, "watch_dir", watch_dir)
    await set_db_setting(session, "export_dir", export_dir)
    await set_db_setting(session, "watch_dir_host", body.watch_dir_host)
    await set_db_setting(session, "export_dir_host", body.export_dir_host)
    await session.commit()

    # Host-Mounts-Datei schreiben
    try:
        _write_host_mounts(body.watch_dir_host, body.export_dir_host)
    except Exception:
        logger.warning("Konnte .host-mounts.json nicht schreiben")

    # Export-Verzeichnis anlegen (falls gesetzt)
    if export_dir:
        from pathlib import Path
        try:
            Path(export_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.warning("Export-Verzeichnis konnte nicht angelegt werden: %s", export_dir)

    # Watch-Task neu starten wenn sich der Pfad geaendert hat
    if watch_dir != old_watch_dir:
        await _restart_watch_task(request.app, settings)
        logger.info("Watch-Ordner geaendert: %s -> %s", old_watch_dir, watch_dir)

    return FolderSettings(
        watch_dir=watch_dir,
        export_dir=export_dir,
        watch_dir_host=body.watch_dir_host,
        export_dir_host=body.export_dir_host,
        restart_required=restart_required,
    )


async def _restart_watch_task(app, settings: Settings) -> None:
    """Stoppt den laufenden Watch-Task und startet ihn mit dem neuen Pfad neu."""
    old_task = getattr(app.state, "watch_task", None)
    if old_task and not old_task.done():
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass

    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        logger.warning("session_factory nicht in app.state – Watch-Task kann nicht neu gestartet werden")
        return

    from app.services.watch_folder_service import run_watch_folder
    new_task = asyncio.create_task(run_watch_folder(session_factory, settings))
    app.state.watch_task = new_task
    logger.info("Watch-Ordner-Task neu gestartet")


@router.get("/system/health")
async def system_health(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Detaillierter System-Gesundheitscheck."""
    components = {}

    # Backend
    components["backend"] = {"status": "ok"}

    # Datenbank
    try:
        await session.execute(text("SELECT 1"))
        components["database"] = {"status": "ok"}
    except Exception as e:
        components["database"] = {"status": "error", "message": str(e)}

    # Ollama
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                components["ollama"] = {
                    "status": "ok",
                    "models": [m["name"] for m in models],
                }
            else:
                components["ollama"] = {"status": "error", "message": f"HTTP {resp.status_code}"}
    except Exception:
        components["ollama"] = {"status": "offline", "message": "Nicht erreichbar"}

    # ChromaDB
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://{settings.CHROMADB_HOST}:{settings.CHROMADB_PORT}/api/v2/heartbeat")
            if resp.status_code == 200:
                from app.services.vectorize_service import get_collection_count
                vec_count = get_collection_count(settings)
                components["chromadb"] = {"status": "ok", "vectors": vec_count}
            else:
                components["chromadb"] = {"status": "error", "message": f"HTTP {resp.status_code}"}
    except Exception:
        components["chromadb"] = {"status": "offline", "message": "Nicht erreichbar"}

    # Speicher-Info
    sys_info = get_system_info(settings)

    # Dokument-Statistiken
    doc_count_result = await session.execute(
        select(func.count()).select_from(Document).where(Document.status != DocumentStatus.DELETED)
    )
    doc_count = doc_count_result.scalar() or 0

    # Installierte Version und Pfad lesen
    from pathlib import Path
    version_file = Path("./data/.version")
    install_path_file = Path("./data/.install-path")
    try:
        app_version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "unknown"
    except Exception:
        app_version = "unknown"
    try:
        install_path = install_path_file.read_text(encoding="utf-8").strip() if install_path_file.exists() else ""
    except Exception:
        install_path = ""

    result = {
        "status": "ok" if all(c.get("status") == "ok" for c in components.values()) else "degraded",
        "app_version": app_version,
        "components": components,
        "statistics": {
            "total_documents": doc_count,
            **sys_info,
        },
    }
    if install_path:
        result["install_path"] = install_path
    return result


@router.post("/system/backup")
async def create_backup_endpoint(
    full: bool = False,
    settings: Settings = Depends(get_settings),
):
    """Erstellt ein Backup."""
    try:
        path = create_backup(settings, include_documents=full)
        return {"path": path, "message": "Backup erstellt"}
    except Exception:
        logger.exception("Backup fehlgeschlagen")
        raise HTTPException(500, "Backup fehlgeschlagen")


@router.get("/system/backups")
async def list_backups_endpoint(settings: Settings = Depends(get_settings)):
    """Listet vorhandene Backups."""
    return {"backups": list_backups(settings)}


@router.get("/system/backups/{filename}")
async def download_backup(
    filename: str,
    settings: Settings = Depends(get_settings),
):
    """Backup-Datei herunterladen."""
    from pathlib import Path
    backup_dir = Path(settings.ARCHIVE_DIR).parent / "backups"
    file_path = backup_dir / filename
    if not file_path.exists() or not file_path.name.startswith("backup_"):
        raise HTTPException(404, "Backup nicht gefunden")
    return FileResponse(file_path, filename=filename, media_type="application/zip")


@router.post("/system/maintenance/optimize-db")
async def optimize_db(session: AsyncSession = Depends(get_db)):
    """Datenbank optimieren (VACUUM)."""
    try:
        await session.execute(text("VACUUM"))
        return {"message": "Datenbank optimiert"}
    except Exception:
        logger.exception("DB-Optimierung fehlgeschlagen")
        raise HTTPException(500, "Optimierung fehlgeschlagen")


@router.post("/system/maintenance/rebuild-index")
async def rebuild_index(session: AsyncSession = Depends(get_db)):
    """FTS5-Suchindex neu aufbauen."""
    try:
        from app.services.search_service import rebuild_fts_index
        count = await rebuild_fts_index(session)
        return {"message": f"Index fuer {count} Dokumente neu aufgebaut"}
    except Exception:
        logger.exception("Index-Rebuild fehlgeschlagen")
        raise HTTPException(500, "Index-Rebuild fehlgeschlagen")


@router.post("/system/maintenance/rebuild-vectors")
async def rebuild_vectors(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Vektor-Index fuer alle Dokumente neu aufbauen."""
    try:
        from app.services.vectorize_service import vectorize_document

        result = await session.execute(
            select(Document).where(Document.status != DocumentStatus.DELETED)
        )
        docs = result.scalars().all()

        total_chunks = 0
        for doc in docs:
            chunks = await vectorize_document(doc, settings)
            total_chunks += chunks

        return {
            "message": f"Vektor-Index fuer {len(docs)} Dokumente aufgebaut ({total_chunks} Chunks)",
            "documents": len(docs),
            "chunks": total_chunks,
        }
    except Exception:
        logger.exception("Vektor-Rebuild fehlgeschlagen")
        raise HTTPException(500, "Vektor-Rebuild fehlgeschlagen")
