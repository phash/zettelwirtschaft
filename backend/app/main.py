import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.api.auth import router as auth_router, is_session_valid, SESSION_COOKIE
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.filing_scopes import router as filing_scopes_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.notifications import router as notifications_router
from app.api.review import router as review_router
from app.api.search import router as search_router
from app.api.system import router as system_router
from app.api.tax import router as tax_router
from app.api.warranties import router as warranties_router
from app.config import get_settings
from app.database import async_session_factory, init_db


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

    # Modelle registrieren (muessen vor init_db importiert sein)
    import app.models.system_setting  # noqa: F401

    # Datenbank initialisieren
    await init_db()
    logger.info("Datenbank initialisiert")

    # FTS5-Index sicherstellen
    from app.services.search_service import ensure_fts_table

    async with async_session_factory() as session:
        await ensure_fts_table(session)
    logger.info("FTS5-Index bereit")

    # Embedding-Modell sicherstellen
    try:
        from app.services.embedding_service import ensure_embedding_model
        if await ensure_embedding_model(settings):
            logger.info("Embedding-Modell bereit")
        else:
            logger.warning("Embedding-Modell konnte nicht geladen werden - Chat nicht verfuegbar")
    except Exception:
        logger.warning("Embedding-Modell-Check fehlgeschlagen", exc_info=True)

    # Background-Tasks starten
    background_tasks: list[asyncio.Task] = []

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
        run_warranty_reminder(async_session_factory, settings)
    )
    background_tasks.append(reminder_task)

    # Auto-Backup
    from app.services.backup_service import run_auto_backup

    backup_task = asyncio.create_task(
        run_auto_backup(async_session_factory, settings)
    )
    background_tasks.append(backup_task)

    # Initiale Vektorisierung (wenn ChromaDB leer)
    async def _initial_vectorize():
        try:
            from app.services.vectorize_service import get_collection_count, vectorize_document
            from app.models.document import Document, DocumentStatus
            from sqlalchemy import select

            count = get_collection_count(settings)
            if count > 0:
                logger.info("ChromaDB enthaelt %d Eintraege, ueberspringe initiale Vektorisierung", count)
                return

            async with async_session_factory() as session:
                result = await session.execute(
                    select(Document).where(Document.status != DocumentStatus.DELETED)
                )
                docs = result.scalars().all()
                if not docs:
                    return

                logger.info("Starte initiale Vektorisierung fuer %d Dokumente...", len(docs))
                total_chunks = 0
                for doc in docs:
                    chunks = await vectorize_document(doc, settings)
                    total_chunks += chunks
                logger.info("Initiale Vektorisierung abgeschlossen: %d Chunks", total_chunks)
        except Exception:
            logger.warning("Initiale Vektorisierung fehlgeschlagen", exc_info=True)

    vectorize_task = asyncio.create_task(_initial_vectorize())
    background_tasks.append(vectorize_task)

    yield

    # Background-Tasks beenden
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    logger.info("Background-Tasks beendet")


app = FastAPI(
    title="Zettelwirtschaft",
    description="Lokales Dokumentenmanagementsystem fuer Privathaushalte",
    version="1.0.2",
    lifespan=lifespan,
)

class PinAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        settings_provider = request.app.dependency_overrides.get(get_settings, get_settings)
        settings = settings_provider()
        if not settings.PIN_ENABLED:
            return await call_next(request)

        path = request.url.path
        if (
            path.startswith("/api/health")
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


app.add_middleware(PinAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
