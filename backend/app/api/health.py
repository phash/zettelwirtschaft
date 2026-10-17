import logging
import shutil

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.rate_limit import limiter
from app.database import get_db
from app.services.ollama_client import ollama_health

router = APIRouter()
logger = logging.getLogger("zettelwirtschaft.health")


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
    response: Response,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """H-03: vom globalen Rate-Limit ausgenommen. docker-compose-Healthchecks
    feuern alle 30s pro Container, plus Frontend-Polling — wuerde sonst
    bei knapp mehreren Clients das 200/min-Limit zerschiessen und einen
    unhealthy-Restart-Loop ausloesen.

    HTTP 503 bei Gesamtstatus "error" (Datenbank/Speicher), damit Docker-
    Healthcheck und ``curl -sf`` in start.bat/update.bat den Ausfall sehen.
    Warnungen (z.B. Ollama/Modell fehlt) bleiben HTTP 200.
    """
    components: dict[str, ComponentStatus] = {}

    # API - immer OK wenn erreichbar
    components["api"] = ComponentStatus(status="ok")

    # Datenbank
    try:
        await db.execute(text("SELECT 1"))
        # AVAIL-03: Transaktion sofort beenden -> Pool-Verbindung geht zurueck,
        # BEVOR unten auf Ollama gewartet wird (bis zu 5 s Netz-I/O). Sonst
        # hielte jeder parallele, unauthentifizierte Health-Aufruf eine der
        # 15 Pool-Verbindungen und blockierte UI, Queue-Worker und Tasks.
        await db.rollback()
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
    except Exception:
        # S-LOW5: keine Pfade/Exception-Texte nach aussen.
        logger.exception("Health-Check: Speicherort nicht verfuegbar")
        components["storage"] = ComponentStatus(status="error", message="Speicherort nicht verfügbar")

    # Ollama (gemeinsam mit /api/system/health, TTL-Cache in ollama_client)
    ollama = await ollama_health(settings)
    components["ollama"] = ComponentStatus(status=ollama.status, message=ollama.message)

    # Gesamtstatus
    statuses = [c.status for c in components.values()]
    if "error" in statuses:
        overall = "error"
        response.status_code = 503
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return HealthResponse(status=overall, **components)
