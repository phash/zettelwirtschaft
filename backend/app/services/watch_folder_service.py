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

# Stabilitaets-Check: zweimal stat() mit Pause; wenn Groesse identisch ist die
# Datei fertig kopiert. Verhindert OCR auf inkomplette Files bei langsamen
# Quellen (USB-Stick, Netzwerk-Share).
_STABILITY_POLL_INTERVAL = 1.0
_STABILITY_MAX_TRIES = 30  # max ~30 s warten bei sehr langsamer Quelle


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
        """Verarbeitet eine neue Datei nach kurzer Wartezeit + Stabilitaets-Check."""
        await asyncio.sleep(_SETTLE_DELAY)

        if not file_path.exists():
            logger.warning("Datei nicht mehr vorhanden: %s", file_path)
            return

        # Stabilitaets-Check: warten bis Groesse zweimal in Folge gleich ist
        # (file_path.stat() in to_thread um Event-Loop nicht zu blockieren).
        previous_size = -1
        for _ in range(_STABILITY_MAX_TRIES):
            try:
                current_size = await asyncio.to_thread(lambda: file_path.stat().st_size)
            except FileNotFoundError:
                logger.warning("Datei wuerd waehrend Stabilitaets-Check entfernt: %s", file_path)
                return
            if current_size == previous_size and current_size > 0:
                break
            previous_size = current_size
            await asyncio.sleep(_STABILITY_POLL_INTERVAL)
        else:
            logger.warning(
                "Datei %s wuchs noch nach %.0f s — verarbeite trotzdem",
                file_path, _STABILITY_MAX_TRIES * _STABILITY_POLL_INTERVAL,
            )

        file_size = previous_size if previous_size > 0 else 0
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


async def _scan_existing_files(
    session_factory, settings: Settings, watch_dir: Path
) -> None:
    """Verarbeitet beim Start alle vorhandenen Dateien im Watch-Ordner."""
    allowed_exts = set(settings.allowed_file_types_list)

    files = [
        f for f in watch_dir.iterdir()
        if f.is_file() and get_file_extension(f.name) in allowed_exts
    ]

    if not files:
        return

    logger.info("Watch-Ordner-Startup-Scan: %d Datei(en) gefunden", len(files))

    for file_path in files:
        try:
            async with session_factory() as session:
                await process_upload(
                    file_path=file_path,
                    original_name=file_path.name,
                    file_size=file_path.stat().st_size,
                    source=JobSource.WATCH_FOLDER,
                    settings=settings,
                    db=session,
                )
                await session.commit()
                logger.info("Watch-Ordner-Startup: Datei eingereicht: %s", file_path.name)

        except FileValidationError as e:
            logger.warning("Startup-Scan: Datei abgelehnt: %s - %s", file_path.name, e.message)
            _move_to_rejected(file_path, settings)

        except Exception:
            logger.exception("Startup-Scan: Fehler bei Datei: %s", file_path.name)
            _move_to_rejected(file_path, settings)


async def run_watch_folder(
    session_factory,
    settings: Settings,
) -> None:
    """Startet die Watch-Ordner-Ueberwachung. Liest den Pfad aus der DB (Fallback: .env)."""
    # Pfad aus DB laden (ermoeglicht UI-Konfiguration ohne .env-Aenderung)
    from app.services.settings_service import get_db_setting
    async with session_factory() as session:
        watch_dir_str = await get_db_setting(session, "watch_dir", settings.WATCH_DIR)
    watch_dir = Path(watch_dir_str)
    watch_dir.mkdir(parents=True, exist_ok=True)

    # Beim Start vorhandene Dateien einlesen
    await _scan_existing_files(session_factory, settings, watch_dir)

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
