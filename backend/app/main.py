import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Native-Windows: Tesseract + poppler aus gebundeltem bin/-Ordner laden, BEVOR
# pdf2image/pytesseract irgendwo importiert werden. Im Docker/Linux no-op.
from app.bin_paths import configure_bundled_binaries
configure_bundled_binaries()

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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
from app.api.reanalysis import router as reanalysis_router
from app.api.review import router as review_router
from app.api.search import router as search_router
from app.api.split import router as split_router, split_error_handler
from app.api.system import router as system_router
from app.api.tasks import router as tasks_router, task_conflict_handler
from app.api.tax import router as tax_router
from app.api.warranties import router as warranties_router
from app.config import get_settings, settings_for_request
from app.core.version import read_app_version
from app.database import async_session_factory
from app.services.split_service import SplitError
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

    # Verzeichnisse erstellen — Watch-/Export-Ordner auf einer Freigabe NICHT:
    # ohne eigene Verbindung meldete sich der Dienst mit dem Computerkonto am
    # Server an (NET-SEC-06). Die legt der Watch-Task bzw. der Export erst nach
    # dem Verbinden an.
    from app.core.data_dirs import ensure_startup_dirs

    for dir_path in ensure_startup_dirs(settings, logger):
        logger.info("Verzeichnis bereit: %s", dir_path)

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

    # Aufzuteilende Stapel (Spezifikation 12): DETECTING ohne Job -> FAILED,
    # halb geschriebene Aufteilungen zurueck auf PROPOSED bzw. fehlende
    # Teil-Jobs anlegen. Nach recover_interrupted_jobs (PROCESSING -> PENDING).
    from app.services.split_service import recover_interrupted_batches

    async with async_session_factory() as session:
        await recover_interrupted_batches(session, settings)

    # Massenlauf der Neu-Analyse (Issue #39): durch den Neustart unterbrochene
    # Dokumente (PROCESSING) zurueck in die Warteschlange — es gibt nur den einen
    # In-Process-Runner, der Lauf macht danach weiter.
    from app.services.reanalysis_run_service import recover_reanalysis_runs

    await recover_reanalysis_runs(async_session_factory)

    # Hintergrund-Vorgaenge (Re-Analyse, E-Mail-Abruf, Backup) laufen nur
    # in-process: beim Start verwaiste PENDING/RUNNING-Eintraege auf FAILED
    # setzen und alte abgeschlossene Eintraege aufraeumen.
    from app.services.task_service import prune_finished_tasks, recover_interrupted_tasks

    await recover_interrupted_tasks(async_session_factory)
    await prune_finished_tasks(async_session_factory)

    # LLM- und Embedding-Modell im Hintergrund sicherstellen (exakter Tag-Abgleich,
    # fehlende Modelle werden gepullt) — ein Pull (Embedding 0,6-2,5 GB, LLM 4-7 GB)
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

    # Massenlauf der Neu-Analyse: nimmt Dokumente nur, wenn keine neuen
    # Eingaenge warten (Vorrang Queue-Worker).
    from app.services.reanalysis_run_service import run_reanalysis_runner

    background_tasks.append(asyncio.create_task(
        run_reanalysis_runner(async_session_factory, settings)
    ))

    # Watch-Ordner
    from app.services.watch_folder_service import run_watch_folder

    watch_task = asyncio.create_task(
        run_watch_folder(async_session_factory, settings)
    )
    background_tasks.append(watch_task)

    # Task-Referenz und Session-Factory in app.state (fuer Watch-Task-Neustart via API)
    app.state.watch_task = watch_task
    app.state.shutting_down = False
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

    # Aufbewahrungsfrist aufgeteilter Stapel (SPLIT_KEEP_SOURCE_DAYS): beim Start
    # und danach taeglich Stapeldateien + Vorschaubilder abgeschlossener Stapel.
    from app.services.split_service import run_split_cleanup

    background_tasks.append(asyncio.create_task(
        run_split_cleanup(async_session_factory, settings)
    ))

    # Initiale Vektorisierung (wenn ChromaDB leer) — Count-Retry und Schutz vor
    # Leer-Fehlinterpretation in vectorize_service.run_initial_vectorize; der
    # Rebuild selbst laeuft als Hintergrund-Vorgang "rebuild_vectors".
    from app.services.vectorize_service import run_initial_vectorize

    vectorize_task = asyncio.create_task(
        run_initial_vectorize(async_session_factory, settings)
    )
    background_tasks.append(vectorize_task)

    yield

    await _shutdown_background(app, background_tasks)
    logger.info("Background-Tasks beendet")

    # H-BE-1: Singleton-LLM-Client sauber schliessen.
    from app.services.ollama_client import aclose_client as aclose_ollama_client
    await aclose_ollama_client()


async def _shutdown_background(app: FastAPI, background_tasks: list[asyncio.Task]) -> None:
    """Hintergrundarbeit in der richtigen Reihenfolge beenden (NET-SEC-04, R1).

    1. Alle Tasks abbrechen und abwarten — auch einen ueber die Einstellungen
       neu gestarteten Watch-Task (er steht nur in ``app.state.watch_task``).
    2. Hintergrund-Vorgaenge abbrechen (werden als FAILED markiert).
    3. Erst dann die Netzwerkordner-Verbindungen trennen: ``shutdown`` nimmt
       keine neuen Verbindungen mehr an und wartet auf laufende Zugriffe
       (Leases), sonst liefen Threads ohne Verbindung (Computerkonto) weiter.
    """
    app.state.shutting_down = True
    tasks = list(background_tasks)
    current_watch = getattr(app.state, "watch_task", None)
    if current_watch is not None and current_watch not in tasks:
        tasks.append(current_watch)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    from app.services.task_service import shutdown_tasks
    await shutdown_tasks()

    # Netzwerkordner-Verbindungen (Watch/Export) trennen — sonst blieben sie
    # in der LocalSystem-Sitzung bis zum Neustart bestehen.
    from app.core import network_share
    await asyncio.to_thread(network_share.connections.shutdown)


# Globales Rate-Limit: 200 Requests/Minute pro Client-IP als App-weite
# Dependency (laeuft nach dem Routing, kennt also den Endpoint fuer
# @limiter.exempt). Ergaenzt das auth-spezifische 5/30s aus N-002 — verhindert
# dass ein einzelner Client das LAN-Backend mit Polling oder Suchen
# ueberlastet. Die Pin-Login-Route behaelt ihren eigenen, strengeren Lockout.
from fastapi import Depends, Request
from app.core.rate_limit import global_rate_limit, limiter
from app.core.host_guard import HostGuardMiddleware
from app.core.request_limits import RequestBodyLimitMiddleware
from app.core.security_headers import SecurityHeadersMiddleware

# AUTH-03: /docs, /redoc, /openapi.json nur auf ausdruecklichen Wunsch
# (ENABLE_API_DOCS) — die PIN-Middleware schuetzt nur /api/*, im Native-Betrieb
# waere die komplette API-Landkarte sonst ohne PIN im LAN abrufbar (und Swagger-UI
# laedt Assets von einem externen CDN).
_docs_enabled = get_settings().ENABLE_API_DOCS

app = FastAPI(
    title="Zettelwirtschaft",
    description="Lokales Dokumentenmanagementsystem fuer Privathaushalte",
    version=read_app_version(),
    lifespan=lifespan,
    dependencies=[Depends(global_rate_limit)],
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

app.add_exception_handler(TaskConflictError, task_conflict_handler)
app.add_exception_handler(SplitError, split_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 wie FastAPI-Standard, aber OHNE das Feld ``input``: Pydantic spiegelt
    dort den gesendeten Wert (bei fehlenden Feldern den ganzen Body) zurueck -
    samt Passwoertern (Netzwerkordner, IMAP). Das Frontend nutzt nur
    ``type``/``loc``/``msg``/``ctx``."""
    errors = [{k: v for k, v in err.items() if k not in ("input", "url")} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


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

_settings = get_settings()
# BR-03: bewusst KEINE CORSMiddleware. Die SPA spricht die API immer same-origin
# an (Docker: nginx-Proxy, Native: StaticFiles, Dev: Vite-Proxy auf :3000). Die
# fruehere Regex (localhost:* + RFC1918, allow_credentials=True) gab jeder
# anderen Port-Origin auf demselben Rechner (same-site -> bekommt das
# SameSite=strict-Session-Cookie) Lesezugriff auf das komplette Archiv.

# AUTH-02: Host-/Origin-/Fetch-Metadata-Pruefung (DNS-Rebinding, CSRF) — zuletzt
# registriert = laeuft zuerst, vor PIN-Middleware und Routen.
app.add_middleware(HostGuardMiddleware)
# BR-04: Security-Header (CSP, X-Frame-Options, nosniff, Referrer-Policy) im
# Native-Betrieb, wo kein nginx davor steht. Aeusserste Schicht, damit auch
# 400/403 des Host-Guards die Header tragen.
app.add_middleware(SecurityHeadersMiddleware)

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
app.include_router(reanalysis_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(email_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(split_router, prefix="/api")


# Native-Setup: Frontend-Build (Vite-`dist/`) direkt vom Backend ausliefern.
# Damit entfaellt der separate nginx-Container. Im Bundle immer
# `<install-dir>/frontend` (INFRA-01, siehe core.install_dir.frontend_dist_dir —
# FRONTEND_DIST_DIR aus der config.toml wird dort ignoriert); Dev: optional
# FRONTEND_DIST_DIR, Docker: leer.
# WICHTIG: Asset-Mount + SPA-Fallback NACH allen `/api`-Routern, sonst
# werden API-Calls von StaticFiles abgefangen.
def _mount_frontend(target: FastAPI, settings) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from app.core.install_dir import frontend_dist_dir

    dist_path = frontend_dist_dir(settings.FRONTEND_DIST_DIR)
    if dist_path is None:
        return
    if settings.FRONTEND_DIST_DIR and Path(settings.FRONTEND_DIST_DIR).resolve() != dist_path.resolve():
        logging.getLogger("zettelwirtschaft").warning(
            "FRONTEND_DIST_DIR=%r wird ignoriert — die Weboberflaeche kommt immer aus dem Programmordner (%s)",
            settings.FRONTEND_DIST_DIR, dist_path,
        )
    if not dist_path.exists():
        return

    index_file = dist_path / "index.html"
    assets_dir = dist_path / "assets"
    if assets_dir.exists():
        target.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # SPA-Fallback: liefert echte Files wenn vorhanden, sonst index.html
    # (Vue-Router uebernimmt das Routing dann clientseitig).
    # Statische SPA-Shell zaehlt nicht gegen das API-Rate-Limit (wie /assets).
    @target.get("/{full_path:path}", include_in_schema=False)
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


_mount_frontend(app, _settings)
