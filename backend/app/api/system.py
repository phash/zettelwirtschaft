"""System-Health und Backup-API."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.document import Document, DocumentStatus
from app.services.backup_service import create_backup, get_system_info, list_backups

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


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

    # Speicher-Info
    sys_info = get_system_info(settings)

    # Dokument-Statistiken
    doc_count_result = await session.execute(
        select(func.count()).select_from(Document).where(Document.status != DocumentStatus.DELETED)
    )
    doc_count = doc_count_result.scalar() or 0

    return {
        "status": "ok" if all(c.get("status") == "ok" for c in components.values()) else "degraded",
        "components": components,
        "statistics": {
            "total_documents": doc_count,
            **sys_info,
        },
    }


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
