import asyncio
import logging
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import Settings
from app.core.file_utils import get_file_extension
from app.models.processing_job import JobSource
from app.services.file_validation_service import FileValidationError, is_allowed_file_type
from app.services.upload_service import discard_upload, process_upload

logger = logging.getLogger("zettelwirtschaft.watch_folder")

# Verzoegerung bevor eine neue Datei verarbeitet wird (Sekunden)
_SETTLE_DELAY = 2.0

# Stabilitaets-Check: zweimal stat() mit Pause; wenn Groesse identisch ist die
# Datei fertig kopiert. Verhindert OCR auf inkomplette Files bei langsamen
# Quellen (USB-Stick, Netzwerk-Share).
_STABILITY_POLL_INTERVAL = 1.0
_STABILITY_MAX_TRIES = 30  # max ~30 s warten bei sehr langsamer Quelle

# Unterordner im Watch-Ordner (werden nicht ueberwacht, recursive=False)
REJECTED_DIRNAME = "rejected"
PROCESSED_DIRNAME = "verarbeitet"

# Zwischendateien von Sync-Clients, Browsern und Scannern — sie werden erst
# nach dem Umbenennen (on_moved) zur eigentlichen Datei.
_TEMP_SUFFIXES = (".tmp", ".part", ".partial", ".crdownload", ".download", ".filepart")

# IO_REPARSE_TAG_MOUNT_POINT (Junction) und IO_REPARSE_TAG_SYMLINK — die
# Konstanten gibt es in ``stat`` nur unter Windows.
_REDIRECTING_REPARSE_TAGS = frozenset({0xA0000003, 0xA000000C})


def _is_candidate(path: Path, settings: Settings) -> bool:
    """True fuer Dateien, die der Watch-Ordner importieren soll.

    Versteckte Dateien, Office-Lockfiles (~$), Temp-/Teil-Downloads und nicht
    erlaubte Typen (desktop.ini, Thumbs.db, ...) werden still ignoriert —
    frueher landeten sie (z.B. eine noch geschriebene .tmp) in rejected/.
    """
    name = path.name
    if name.startswith(".") or name.startswith("~$"):
        return False
    if name.lower().endswith(_TEMP_SUFFIXES):
        return False
    return is_allowed_file_type(get_file_extension(name), settings)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _is_default_watch_dir(watch_dir: Path, settings: Settings) -> bool:
    """App-verwalteter Standard-Watch-Ordner (settings.WATCH_DIR) — nur dort
    werden Originale nach dem Import geloescht."""
    return _same_dir(watch_dir, Path(settings.WATCH_DIR))


def _unique_destination(target_dir: Path, name: str) -> Path:
    """Zielpfad ohne Ueberschreiben: name, sonst name_<Zeitstempel>[_n]."""
    dest = target_dir / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = target_dir / f"{stem}_{stamp}{suffix}"
    counter = 1
    while dest.exists():
        dest = target_dir / f"{stem}_{stamp}_{counter}{suffix}"
        counter += 1
    return dest


def _is_link(path: Path) -> bool:
    """True fuer Symlinks, Junctions und andere Reparse-Points (ohne zu folgen).

    FH-04: Der Native-Dienst laeuft als LocalSystem. Liegt der Watch-Ordner in
    einem fuer andere lokale Benutzer beschreibbaren Ordner, koennten diese
    ``verarbeitet``/``rejected`` als Junction (braucht keine Rechte) auf einen
    beliebigen Systemordner anlegen — der Dienst verschoebe Dateien dann mit
    SYSTEM-Rechten dorthin. Ebenso wenig darf er verlinkten Quelldateien folgen.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    # Nur umleitende Reparse-Points (Junction/Mount-Point, Symlink) — andere
    # Tags wie OneDrive-/Cloud-Dateien oder Deduplizierung sind normale Dateien.
    return getattr(st, "st_reparse_tag", 0) in _REDIRECTING_REPARSE_TAGS


def _move_into(file_path: Path, watch_dir: Path, dirname: str) -> Path | None:
    """Verschiebt ``file_path`` nach ``<watch_dir>/<dirname>`` — nur wenn dieser
    Ordner ein echtes Unterverzeichnis des Watch-Ordners ist (FH-04)."""
    target_dir = watch_dir / dirname
    try:
        target_dir.mkdir(exist_ok=True)
        if (
            _is_link(target_dir)
            or not target_dir.is_dir()
            or target_dir.resolve().parent != watch_dir.resolve()
        ):
            logger.error(
                "%s ist kein echter Unterordner des Watch-Ordners (Link/Junction?) — "
                "Datei %s bleibt liegen", target_dir, file_path.name,
            )
            return None
        if _is_link(file_path):
            logger.error("Verlinkte Datei wird nicht verschoben: %s", file_path)
            return None
        dest = _unique_destination(target_dir, file_path.name)
        # TODO(FH-04): Zwischen Pruefung und Verschieben bleibt ein kurzes
        # TOCTOU-Fenster (Ordner gegen Junction tauschen). Saubere Loesung:
        # Dienst nicht als LocalSystem betreiben (siehe service-install.bat).
        shutil.move(str(file_path), str(dest))
        return dest
    except Exception:
        logger.exception("Fehler beim Verschieben von %s nach %s", file_path.name, target_dir)
        return None


def _move_to_rejected(file_path: Path, watch_dir: Path) -> None:
    """Verschiebt eine abgelehnte Datei nach <watch_dir>/rejected (ohne
    gleichnamige fruehere Ablehnungen zu ueberschreiben)."""
    if _move_into(file_path, watch_dir, REJECTED_DIRNAME):
        logger.info("Datei verschoben nach rejected: %s", file_path.name)


def _finish_original(file_path: Path, watch_dir: Path, settings: Settings) -> None:
    """Original nach erfolgreichem Import aufraeumen (M4: erst nach dem Commit).

    Im app-verwalteten Standard-Watch-Ordner wird es geloescht. Vom Benutzer
    gewaehlte Ordner enthalten seine eigenen Dateien — dort wird nie geloescht,
    sondern nach <watch_dir>/verarbeitet verschoben.
    """
    if _is_default_watch_dir(watch_dir, settings):
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Quelldatei konnte nicht entfernt werden: %s", file_path, exc_info=True)
        return
    if _move_into(file_path, watch_dir, PROCESSED_DIRNAME):
        logger.info("Original verschoben nach %s: %s", PROCESSED_DIRNAME, file_path.name)


async def _import_file(file_path: Path, file_size: int, watch_dir: Path, session_factory, settings: Settings) -> None:
    """Gemeinsamer Import-Pfad fuer Laufzeit-Events und Startup-Scan."""
    original_name = file_path.name
    # Erst hier (nach Settle-Delay/Stabilitaets-Check) pruefen: die Datei kann
    # zwischen Event und Import gegen einen Link getauscht worden sein (FH-04).
    if await asyncio.to_thread(_is_link, file_path):
        logger.warning("Verlinkte Datei im Watch-Ordner ignoriert: %s", file_path)
        return
    try:
        async with session_factory() as session:
            job = await process_upload(
                file_path=file_path,
                original_name=original_name,
                file_size=file_size,
                source=JobSource.WATCH_FOLDER,
                settings=settings,
                db=session,
            )
            try:
                await session.commit()
            except Exception:
                discard_upload(job)
                raise
        await asyncio.to_thread(_finish_original, file_path, watch_dir, settings)
        logger.info("Watch-Ordner-Datei eingereicht: %s", original_name)

    except FileValidationError as e:
        logger.warning("Datei abgelehnt: %s - %s", original_name, e.message)
        await asyncio.to_thread(_move_to_rejected, file_path, watch_dir)

    except Exception:
        logger.exception("Fehler bei Verarbeitung von Watch-Ordner-Datei: %s", original_name)
        await asyncio.to_thread(_move_to_rejected, file_path, watch_dir)


class _WatchHandler(FileSystemEventHandler):
    """Reagiert auf neue und umbenannte/verschobene Dateien im Watch-Ordner."""

    def __init__(self, settings: Settings, session_factory, loop: asyncio.AbstractEventLoop, watch_dir: Path):
        super().__init__()
        self.settings = settings
        self.session_factory = session_factory
        self.loop = loop
        self.watch_dir = watch_dir
        # Pfade in Bearbeitung (nur im Event-Loop gelesen/geschrieben) —
        # created + moved bzw. doppelte Events loesen keinen Doppelimport aus.
        self._in_flight: set[str] = set()

    def _schedule(self, file_path: Path) -> None:
        if not _same_dir(file_path.parent, self.watch_dir):
            return  # Unterordner (rejected/, verarbeitet/) oder ausserhalb
        if not _is_candidate(file_path, self.settings):
            return
        logger.info("Neue Datei erkannt: %s", file_path.name)
        asyncio.run_coroutine_threadsafe(self._handle_new_file(file_path), self.loop)

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        self._schedule(Path(event.src_path))

    def on_moved(self, event: FileMovedEvent) -> None:
        # Sync-Clients/Scanner schreiben eine Temp-Datei und benennen sie um;
        # auch hineinverschobene Dateien kommen als Move-Event an.
        if event.is_directory:
            return
        self._schedule(Path(event.dest_path))

    async def _handle_new_file(self, file_path: Path) -> None:
        """Verarbeitet eine neue Datei nach kurzer Wartezeit + Stabilitaets-Check."""
        key = str(file_path)
        if key in self._in_flight:
            return
        self._in_flight.add(key)
        try:
            await self._process_when_stable(file_path)
        finally:
            self._in_flight.discard(key)

    async def _process_when_stable(self, file_path: Path) -> None:
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
                logger.warning("Datei wurde während Stabilitäts-Check entfernt: %s", file_path)
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
        await _import_file(file_path, file_size, self.watch_dir, self.session_factory, self.settings)


async def _scan_existing_files(
    session_factory, settings: Settings, watch_dir: Path
) -> None:
    """Verarbeitet beim Start alle vorhandenen Dateien im Watch-Ordner."""
    # M5: Verzeichnis-Walk + stat() sind blockierend — im Thread ausfuehren,
    # damit der Event-Loop beim Start (langsamer USB-Stick/Netzwerk-Share) nicht
    # haengt.
    def _list_files() -> list[Path]:
        return [
            f for f in watch_dir.iterdir()
            if not _is_link(f) and f.is_file() and _is_candidate(f, settings)
        ]

    files = await asyncio.to_thread(_list_files)

    if not files:
        return

    logger.info("Watch-Ordner-Startup-Scan: %d Datei(en) gefunden", len(files))

    for file_path in files:
        try:
            file_size = await asyncio.to_thread(lambda p=file_path: p.stat().st_size)
        except FileNotFoundError:
            continue
        await _import_file(file_path, file_size, watch_dir, session_factory, settings)


async def resolve_watch_dir(session_factory, settings: Settings) -> Path:
    """Effektiver Watch-Ordner: DB-Einstellung (UI), Fallback settings.WATCH_DIR."""
    from app.services.settings_service import get_db_setting

    async with session_factory() as session:
        watch_dir_str = await get_db_setting(session, "watch_dir", settings.WATCH_DIR)
    return Path(watch_dir_str or settings.WATCH_DIR)


async def run_watch_folder(
    session_factory,
    settings: Settings,
) -> None:
    """Startet die Watch-Ordner-Ueberwachung. Liest den Pfad aus der DB (Fallback: .env)."""
    watch_dir = await resolve_watch_dir(session_factory, settings)
    watch_dir.mkdir(parents=True, exist_ok=True)

    # Beim Start vorhandene Dateien einlesen
    await _scan_existing_files(session_factory, settings, watch_dir)

    loop = asyncio.get_running_loop()
    handler = _WatchHandler(settings, session_factory, loop, watch_dir)
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
