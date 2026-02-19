import asyncio
import logging
import shutil
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import Settings
from app.core.file_utils import get_file_extension
from app.models.processing_job import JobSource
from app.services.file_validation_service import FileValidationError
from app.services.upload_service import process_upload

logger = logging.getLogger("zettelwirtschaft.watch_folder")

# Verzoegerung bevor eine neue Datei verarbeitet wird (Sekunden)
_SETTLE_DELAY = 2.0


class _WatchHandler(FileSystemEventHandler):
    """Reagiert auf neue Dateien im Watch-Ordner."""

    def __init__(self, settings: Settings, session_factory, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.settings = settings
        self.session_factory = session_factory
        self.loop = loop

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        file_path = Path(event.src_path)
        # rejected-Ordner ignorieren
        if "rejected" in file_path.parts:
            return
        logger.info("Neue Datei erkannt: %s", file_path.name)
        asyncio.run_coroutine_threadsafe(
            self._handle_new_file(file_path),
            self.loop,
        )

    async def _handle_new_file(self, file_path: Path) -> None:
        """Verarbeitet eine neue Datei nach kurzer Wartezeit."""
        await asyncio.sleep(_SETTLE_DELAY)

        if not file_path.exists():
            logger.warning("Datei nicht mehr vorhanden: %s", file_path)
            return

        file_size = file_path.stat().st_size
        original_name = file_path.name

        try:
            async with self.session_factory() as session:
                await process_upload(
                    file_path=file_path,
                    original_name=original_name,
                    file_size=file_size,
                    source=JobSource.WATCH_FOLDER,
                    settings=self.settings,
                    db=session,
                )
                await session.commit()
                logger.info("Watch-Ordner-Datei eingereicht: %s", original_name)

        except FileValidationError as e:
            logger.warning("Datei abgelehnt: %s - %s", original_name, e.message)
            _move_to_rejected(file_path, self.settings)

        except Exception:
            logger.exception("Fehler bei Verarbeitung von Watch-Ordner-Datei: %s", original_name)
            _move_to_rejected(file_path, self.settings)


def _move_to_rejected(file_path: Path, settings: Settings) -> None:
    """Verschiebt eine abgelehnte Datei in den rejected-Ordner."""
    rejected_dir = Path(settings.WATCH_DIR) / "rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    dest = rejected_dir / file_path.name
    try:
        shutil.move(str(file_path), str(dest))
        logger.info("Datei verschoben nach rejected: %s", file_path.name)
    except Exception:
        logger.exception("Fehler beim Verschieben nach rejected: %s", file_path.name)


async def run_watch_folder(
    session_factory,
    settings: Settings,
) -> None:
    """Startet die Watch-Ordner-Ueberwachung."""
    watch_dir = Path(settings.WATCH_DIR)
    watch_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()
    handler = _WatchHandler(settings, session_factory, loop)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()
    logger.info("Watch-Ordner-Ueberwachung gestartet: %s", watch_dir)

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Watch-Ordner-Ueberwachung wird beendet")
        observer.stop()
        observer.join()
