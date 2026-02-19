import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
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

    # Datenbank initialisieren
    await init_db()
    logger.info("Datenbank initialisiert")

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

    yield

    # Background-Tasks beenden
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    logger.info("Background-Tasks beendet")


app = FastAPI(
    title="Zettelwirtschaft",
    description="Lokales Dokumentenmanagementsystem fuer Privathaushalte",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
