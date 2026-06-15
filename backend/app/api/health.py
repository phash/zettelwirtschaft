import logging
import shutil
import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.rate_limit import limiter
from app.database import get_db
from fastapi import Request

router = APIRouter()
logger = logging.getLogger("zettelwirtschaft.health")


# M-2 (Re-Review): Ollama-Subcheck mit TTL-Cache. Docker-Healthcheck feuert
# alle 30s pro Container + Frontend-Polling — jeder Probe ohne Cache wuerde
# Ollama mit HTTP-Calls beschaeftigen. 5s-TTL ist deutlich kuerzer als
# Ollama-Failure-Window, schont das Backend trotzdem.
_ollama_cache: tuple[float, str, str | None] | None = None  # (timestamp, status, message)
_OLLAMA_TTL = 5.0


async def _ollama_status(settings: Settings) -> tuple[str, str | None]:
    global _ollama_cache
    now = time.monotonic()
    if _ollama_cache is not None and (now - _ollama_cache[0]) < _OLLAMA_TTL:
        return _ollama_cache[1], _ollama_cache[2]
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                status, msg = "ok", None
            else:
                status, msg = "warning", f"Ollama antwortet mit Status {resp.status_code}"
    except Exception:
        status, msg = "warning", "Ollama nicht erreichbar"
    _ollama_cache = (now, status, msg)
    return status, msg


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
@limiter.exempt
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """H-03: vom slowapi-Default-Limit ausgenommen. docker-compose-Healthchecks
    feuern alle 30s pro Container, plus Frontend-Polling — wuerde sonst
    bei knapp mehreren Clients das 200/min-Limit zerschiessen und einen
    unhealthy-Restart-Loop ausloesen."""
    components: dict[str, ComponentStatus] = {}

    # API - immer OK wenn erreichbar
    components["api"] = ComponentStatus(status="ok")

    # Datenbank
    try:
        await db.execute(text("SELECT 1"))
        components["database"] = ComponentStatus(status="ok")
    except Exception:
        # S-LOW5: /api/health ist unauthentifiziert — keine internen
        # Exception-Details (Pfade/Treiber) nach aussen geben, nur generisch.
        logger.exception("Health-Check: Datenbank nicht erreichbar")
        components["database"] = ComponentStatus(status="error", message="Datenbankfehler")

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

    # Ollama (M-2: TTL-Cache, siehe _ollama_status)
    ollama_status, ollama_msg = await _ollama_status(settings)
    components["ollama"] = ComponentStatus(status=ollama_status, message=ollama_msg)

    # Gesamtstatus
    statuses = [c.status for c in components.values()]
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return HealthResponse(status=overall, **components)
