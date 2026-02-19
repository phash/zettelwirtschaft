import shutil

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db

router = APIRouter()


class ComponentStatus(BaseModel):
    status: str  # "ok", "warning", "error"
    message: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok", "warning", "error"
    api: ComponentStatus
    database: ComponentStatus
    storage: ComponentStatus
    ollama: ComponentStatus


@router.get("/health", response_model=HealthResponse)
async def health_check(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    components: dict[str, ComponentStatus] = {}

    # API - immer OK wenn erreichbar
    components["api"] = ComponentStatus(status="ok")

    # Datenbank
    try:
        await db.execute(text("SELECT 1"))
        components["database"] = ComponentStatus(status="ok")
    except Exception as e:
        components["database"] = ComponentStatus(status="error", message=str(e))

    # Speicherplatz
    try:
        usage = shutil.disk_usage(settings.ARCHIVE_DIR)
        free_gb = usage.free / (1024**3)
        if free_gb < 1.0:
            components["storage"] = ComponentStatus(
                status="warning",
                message=f"Weniger als 1 GB freier Speicher ({free_gb:.1f} GB)",
            )
        else:
            components["storage"] = ComponentStatus(
                status="ok",
                message=f"{free_gb:.1f} GB frei",
            )
    except Exception as e:
        components["storage"] = ComponentStatus(status="error", message=str(e))

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                components["ollama"] = ComponentStatus(status="ok")
            else:
                components["ollama"] = ComponentStatus(
                    status="warning",
                    message=f"Ollama antwortet mit Status {resp.status_code}",
                )
    except Exception:
        components["ollama"] = ComponentStatus(
            status="warning",
            message="Ollama nicht erreichbar",
        )

    # Gesamtstatus
    statuses = [c.status for c in components.values()]
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return HealthResponse(status=overall, **components)
