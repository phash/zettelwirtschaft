import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Native-Windows: Tesseract + poppler aus gebundeltem bin/-Ordner laden, BEVOR
# pdf2image/pytesseract irgendwo importiert werden. Im Docker/Linux no-op.
from app.bin_paths import configure_bundled_binaries
configure_bundled_binaries()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.api.auth import router as auth_router, is_session_valid, SESSION_COOKIE
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.email import router as email_router
from app.api.filing_scopes import router as filing_scopes_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.notifications import router as notifications_router
from app.api.review import router as review_router
from app.api.search import router as search_router
from app.api.system import router as system_router
from app.api.tasks import router as tasks_router, task_conflict_handler
from app.api.tax import router as tax_router
from app.api.warranties import router as warranties_router
from app.config import get_settings, settings_for_request
from app.core.version import read_app_version
from app.database import async_session_factory
from app.services.task_service import TaskConflictError


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Logging konfigurieren
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("zettelwirtschaft")

    # Verzeichnisse erstellen
    for dir_path in [
        settings.UPLOAD_DIR,
        settings.WATCH_DIR,
        settings.ARCHIVE_DIR,
        settings.THUMBNAIL_DIR,
    ]:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        logger.info("Verzeichnis bereit: %s", dir_path)

    if settings.EXPORT_DIR:
        Path(settings.EXPORT_DIR).mkdir(parents=True, exist_ok=True)
        logger.info("Export-Verzeichnis bereit: %s", settings.EXPORT_DIR)

    # DB-Schema wird vor dem App-Start migriert: Docker ueber entrypoint.sh ->
    # migrate.py, Native ueber app/entrypoint.py (beide Alembic).

    # FTS5-Index sicherstellen
    from app.services.search_service import ensure_fts_table

    async with async_session_factory() as session:
        await ensure_fts_table(session)
    logger.info("FTS5-Index bereit")

    # Sicherheitshinweis bei deaktiviertem PIN-Schutz (N-007).
    # Der Default ist `False` und wird nicht geaendert (Breaking Change fuer
    # Bestandsuser), aber ein lautstarker Warn-Log macht das Risiko sichtbar.
    if not settings.PIN_ENABLED:
        logger.warning(
            "SICHERHEITSHINWEIS: PIN-Schutz ist deaktiviert (PIN_ENABLED=false). "
            "Alle Dokumente und Einstellungen sind im Heimnetz ohne Passwort "
            "zugaenglich. Aktivieren in .env bzw. config.toml: PIN_ENABLED=true + "
            "PIN_CODE=xxxx, danach Dienst bzw. Backend-Container neu starten."
        )

    # Stuck-Job-Recovery: durch den Neustart unterbrochene Jobs (PROCESSING)
    # zuruecksetzen — es gibt nur den einen In-Process-Worker.
    from app.services.queue_worker_service import recover_interrupted_jobs

    async with async_session_factory() as session:
        await recover_interrupted_jobs(session, settings)

    # Hintergrund-Vorgaenge (Re-Analyse, E-Mail-Abruf, Backup) laufen nur
    # in-process: beim Start verwaiste PENDING/RUNNING-Eintraege auf FAILED
    # setzen und alte abgeschlossene Eintraege aufraeumen.
    from app.services.task_service import prune_finished_tasks, recover_interrupted_tasks

    await recover_interrupted_tasks(async_session_factory)
    await prune_finished_tasks(async_session_factory)

    # LLM- und Embedding-Modell im Hintergrund sicherstellen (exakter Tag-Abgleich,
    # fehlende Modelle werden gepullt) — ein Pull (bge-m3 ~1.2 GB, LLM mehrere GB)
    # kann Minuten dauern und darf den HTTP-Server nicht blockieren. Bis dahin
    # landen Dokumente mit klarer Rueckfrage ("Modell nicht installiert") in der
    # Pruefung; der Health-Check meldet "wird heruntergeladen".
    async def _ensure_models_bg():
        from app.services.ollama_client import ensure_configured_models
        results = await ensure_configured_models(settings)
        missing = [name for name, ok in results.items() if not ok]
        if missing:
            logger.warning("Ollama-Modelle nicht verfuegbar: %s", ", ".join(missing))
        else:
            logger.info("Ollama-Modelle bereit: %s", ", ".join(results))

    # Background-Tasks starten
    background_tasks: list[asyncio.Task] = []

    # Modell-Check als ersten Background-Task (non-blocking)
    background_tasks.append(asyncio.create_task(_ensure_models_bg()))

    # Queue-Worker
    from app.services.queue_worker_service import run_queue_worker

    queue_task = asyncio.create_task(
        run_queue_worker(async_session_factory, settings)
    )
    background_tasks.append(queue_task)

    # Watch-Ordner
    from app.services.watch_folder_service import run_watch_folder

    watch_task = asyncio.create_task(
        run_watch_folder(async_session_factory, settings)
    )
    background_tasks.append(watch_task)

    # Task-Referenz und Session-Factory in app.state (fuer Watch-Task-Neustart via API)
    app.state.watch_task = watch_task
    app.state.session_factory = async_session_factory

    # Garantie-Reminder
    from app.services.warranty_reminder_service import run_warranty_reminder

    reminder_task = asyncio.create_task(
        run_warranty_reminder(async_session_factory)
    )
    background_tasks.append(reminder_task)

    # Auto-Backup
    from app.services.backup_service import run_auto_backup

    backup_task = asyncio.create_task(
        run_auto_backup(async_session_factory, settings)
    )
    background_tasks.append(backup_task)

    # E-Mail-Scheduler
    from app.services.email_scheduler_service import run_email_scheduler

    email_task = asyncio.create_task(
        run_email_scheduler(async_session_factory, settings)
    )
    background_tasks.append(email_task)

    # Initiale Vektorisierung (wenn ChromaDB leer) — Count-Retry und Schutz vor
    # Leer-Fehlinterpretation in vectorize_service.run_initial_vectorize; der
    # Rebuild selbst laeuft als Hintergrund-Vorgang "rebuild_vectors".
    from app.services.vectorize_service import run_initial_vectorize

    vectorize_task = asyncio.create_task(
        run_initial_vectorize(async_session_factory, settings)
    )
    background_tasks.append(vectorize_task)

    yield

    # Background-Tasks beenden
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    logger.info("Background-Tasks beendet")

    # Laufende Hintergrund-Vorgaenge abbrechen (werden als FAILED markiert).
    from app.services.task_service import shutdown_tasks
    await shutdown_tasks()

    # H-BE-1: Singleton-LLM-Client sauber schliessen.
    from app.services.ollama_client import aclose_client as aclose_ollama_client
    await aclose_ollama_client()


# Globales Rate-Limit: 200 Requests/Minute pro Client-IP als App-weite
# Dependency (laeuft nach dem Routing, kennt also den Endpoint fuer
# @limiter.exempt). Ergaenzt das auth-spezifische 5/30s aus N-002 — verhindert
# dass ein einzelner Client das LAN-Backend mit Polling oder Suchen
# ueberlastet. Die Pin-Login-Route behaelt ihren eigenen, strengeren Lockout.
from fastapi import Depends, Request
from app.core.rate_limit import global_rate_limit, limiter
from app.core.request_limits import RequestBodyLimitMiddleware

app = FastAPI(
    title="Zettelwirtschaft",
    description="Lokales Dokumentenmanagementsystem fuer Privathaushalte",
    version=read_app_version(),
    lifespan=lifespan,
    dependencies=[Depends(global_rate_limit)],
)

app.add_exception_handler(TaskConflictError, task_conflict_handler)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Unerwartete Fehler einheitlich als JSON ``{"detail": ...}`` (statt
    Starlettes text/plain "Internal Server Error") — gleicher Vertrag wie alle
    anderen API-Fehler. Details nur ins Log, nicht an den Client."""
    logging.getLogger("zettelwirtschaft").error(
        "Unbehandelter Fehler bei %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(status_code=500, content={"detail": "Interner Serverfehler"})


class PinAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        settings = settings_for_request(request)
        if not settings.PIN_ENABLED:
            return await call_next(request)

        path = request.url.path
        # S-MED2: Nur die /api/-Daten-Endpunkte schuetzen. Die statische
        # Frontend-Shell (Native-Mode: vom Backend via _mount_frontend
        # ausgeliefert — index.html, /assets/*, SPA-Routen) sowie Health/Auth
        # muessen ohne Session erreichbar sein, sonst laesst sich im Native-Mode
        # bei aktivem PIN die Login-Seite selbst nicht laden (Fail-closed-DoS).
        # In Docker sieht das Backend ohnehin nur /api/* (nginx liefert die SPA).
        if (
            not path.startswith("/api/")
            or path.startswith("/api/health")
            or path.startswith("/api/auth")
        ):
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE)
        if not is_session_valid(token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Nicht authentifiziert"},
            )

        return await call_next(request)


# Request-Body-Obergrenze (413) — im Native-Betrieb gibt es kein nginx mit
# client_max_body_size davor, uvicorn wuerde beliebig grosse Uploads puffern.
# Bewusst VOR PinAuthMiddleware registriert (= innerer Layer): die 413 beim
# Streamen entsteht im receive() und darf nicht durch die Task-Gruppe der
# BaseHTTPMiddleware laufen (sonst wird daraus ein generisches 400).
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(PinAuthMiddleware)

# CORS: LAN-Zugriff erlauben (Home-Netzwerk, kein Cloud)
_settings = get_settings()
# M-1 (Re-Review): CORS-Regex auf localhost + private RFC1918-Bereiche (jeder
# Port, http/https) — eine zusaetzliche allow_origins-Liste waere redundant.
# Hinweis: `allow_credentials=True` + breite Origin-Regex bedeutet, dass jede
# Seite im selben LAN, die Cookies von zettelwirtschaft.local hat, Requests
# senden kann. Im Heim-LAN akzeptabel, im Firmennetz problematisch — siehe
# README/CLAUDE.md fuer den Trade-off.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(filing_scopes_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(search_router, prefix="/api")
app.include_router(tax_router, prefix="/api")
app.include_router(warranties_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(review_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(email_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")


# Native-Setup: Frontend-Build (Vite-`dist/`) direkt vom Backend ausliefern.
# Damit entfaellt der separate nginx-Container. FRONTEND_DIST_DIR wird vom
# Installer auf `<install-dir>/frontend/dist` gesetzt; im Dev/Docker leer.
# WICHTIG: Asset-Mount + SPA-Fallback NACH allen `/api`-Routern, sonst
# werden API-Calls von StaticFiles abgefangen.
def _mount_frontend() -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    dist_dir = _settings.FRONTEND_DIST_DIR
    if not dist_dir:
        return
    dist_path = Path(dist_dir)
    if not dist_path.exists():
        return

    index_file = dist_path / "index.html"
    assets_dir = dist_path / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # SPA-Fallback: liefert echte Files wenn vorhanden, sonst index.html
    # (Vue-Router uebernimmt das Routing dann clientseitig).
    # Statische SPA-Shell zaehlt nicht gegen das API-Rate-Limit (wie /assets).
    @app.get("/{full_path:path}", include_in_schema=False)
    @limiter.exempt
    async def _spa_fallback(full_path: str, request: Request):
        # Bekannte /api/*-Routen fangen die Router oben ab. Unbekannte API-Pfade
        # landen aber hier und duerfen NICHT index.html (200, text/html) liefern —
        # sonst bekommt der API-Client HTML statt eines 404-JSON (wie im Docker-
        # Betrieb ohne Catch-All).
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist_path / full_path).resolve()
        # Path-Traversal-Schutz: nur innerhalb dist
        try:
            candidate.relative_to(dist_path.resolve())
        except ValueError:
            return FileResponse(str(index_file))
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index_file))


_mount_frontend()
