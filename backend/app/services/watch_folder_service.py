import asyncio
import logging
import os
import shutil
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserverVFS

from app.config import Settings
from app.core import network_share
from app.core.file_utils import get_file_extension
from app.models.processing_job import JobSource
from app.services import network_folder_service
from app.services.file_validation_service import FileValidationError, is_allowed_file_type
from app.services.upload_service import SourceUnavailableError, discard_upload, process_upload

logger = logging.getLogger("zettelwirtschaft.watch_folder")

WATCH = network_folder_service.WATCH

# Netzwerk-Watch-Ordner: Polling-Intervall (UI-Einstellung "watch_poll_interval")
POLL_INTERVAL_KEY = "watch_poll_interval"
DEFAULT_POLL_INTERVAL = 30
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 600
# Wiederverbinden: 30 s, 60 s, 120 s ... hoechstens 15 min
BACKOFF_START = 30.0
BACKOFF_MAX = 900.0
_PROBE_TIMEOUT = 20.0
# Laufende Importe vor dem Trennen abwarten. Sie werden danach NICHT
# abgebrochen: jeder Import haelt seine Verbindung selbst (Lease), getrennt
# wird erst nach dem letzten Zugriff (NET-SEC-02, R6). Kurz genug, dass ein
# Speichern der Einstellungen im Zeitlimit der Oberflaeche bleibt (R8).
_DRAIN_TIMEOUT = 30.0
_OBSERVER_JOIN_TIMEOUT = 10.0
# Lokaler Ordner: Pruefung (Observer lebt, keine Verknuepfung auf eine
# Freigabe) jede Sekunde.
_LOCAL_CHECK_INTERVAL = 1.0
# Ordner regelmaessig neu einlesen: holt Dateien, die beim ersten Mal noch
# geschrieben wurden (R5), und alles, was ein Ereignis verpasst hat.
_RESCAN_INTERVAL = 300.0
# Gleiche Fehlermeldung hoechstens alle 15 min erneut loggen
_LOG_REPEAT_SECONDS = 900.0

# Verzoegerung bevor eine neue Datei verarbeitet wird (Sekunden)
_SETTLE_DELAY = 2.0

# Stabilitaets-Check (R5): Groesse UND Aenderungszeit muessen ueber mehrere
# Abfragen gleich bleiben, und kein anderer Prozess darf die Datei noch offen
# haben (Scanner schreiben mehrseitige PDFs mit Pausen zwischen den Seiten).
# Waechst die Datei nach der Hoechstdauer noch, bleibt sie liegen und wird
# beim naechsten Ereignis bzw. Neu-Einlesen erneut versucht — nie halb importiert.
_STABILITY_POLL_INTERVAL = 1.0
_STABILITY_QUIET_POLLS = 3
_STABILITY_MAX_POLLS = 600  # ~10 min

# Unterordner im Watch-Ordner (werden nicht ueberwacht, recursive=False)
REJECTED_DIRNAME = "rejected"
PROCESSED_DIRNAME = "verarbeitet"

# Zwischendateien von Sync-Clients, Browsern und Scannern — sie werden erst
# nach dem Umbenennen (on_moved) zur eigentlichen Datei.
_TEMP_SUFFIXES = (".tmp", ".part", ".partial", ".crdownload", ".download", ".filepart")

# IO_REPARSE_TAG_MOUNT_POINT (Junction) und IO_REPARSE_TAG_SYMLINK — die
# Konstanten gibt es in ``stat`` nur unter Windows.
_REDIRECTING_REPARSE_TAGS = frozenset({0xA0000003, 0xA000000C})

# Ergebnis eines Imports
IMPORTED = "imported"
IMPORTED_LEFT = "imported_left"  # importiert, Original konnte nicht weggeraeumt werden
REJECTED = "rejected"
SKIPPED = "skipped"              # bleibt liegen (Link, voruebergehend nicht lesbar ...)


class _Access:
    """Zugriffsrahmen eines Watch-Ordners.

    Lokal: einfache Threads. Netzwerk (``target=WATCH``): jeder blockierende
    Zugriff laeuft im Thread unter einer eigenen Lease — die Verbindung
    bleibt bestehen, bis der Thread fertig ist, auch wenn der wartende Task
    vorher abgebrochen wurde (NET-SEC-02). Ohne intakte Verbindung wird der
    Pfad nicht angefasst (``ShareError``).
    """

    def __init__(self, target: str | None) -> None:
        self.target = target

    def open(self) -> network_share.Lease:
        if self.target is None:
            return network_share.NO_LEASE
        return network_share.connections.open_lease(self.target)

    def _call(self, fn, args, kwargs):
        with self.open():
            return fn(*args, **kwargs)

    async def run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(self._call, fn, args, kwargs)


LOCAL = _Access(None)


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
    if network_share.is_unc(str(watch_dir)) != network_share.is_unc(settings.WATCH_DIR):
        return False  # Netzwerk- und lokaler Pfad: nie derselbe Ordner (kein resolve noetig)
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


def _file_signature(path: Path) -> tuple[int, int] | None:
    """(Groesse, mtime_ns) ohne Links zu folgen; None = nicht (mehr) vorhanden."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _is_locked(path: Path) -> bool:
    """True, solange ein anderer Prozess die Datei noch offen hat (Windows:
    exklusives Oeffnen scheitert mit Freigabe-/Sperrverletzung)."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes as w

    k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    create = k32.CreateFileW
    create.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    create.restype = w.HANDLE
    generic_read, open_existing, attr_normal = 0x80000000, 3, 0x80
    handle = create(str(path), generic_read, 0, None, open_existing, attr_normal, None)
    if handle is None or handle == w.HANDLE(-1).value:
        return ctypes.get_last_error() in (32, 33)  # SHARING_VIOLATION, LOCK_VIOLATION
    k32.CloseHandle.argtypes = [w.HANDLE]
    k32.CloseHandle(handle)
    return False


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


def _finish_original(file_path: Path, watch_dir: Path, settings: Settings) -> bool:
    """Original nach erfolgreichem Import aufraeumen (M4: erst nach dem Commit).

    Im app-verwalteten Standard-Watch-Ordner wird es geloescht. Vom Benutzer
    gewaehlte Ordner enthalten seine eigenen Dateien — dort wird nie geloescht,
    sondern nach <watch_dir>/verarbeitet verschoben. False = Original liegt noch.
    """
    if _is_default_watch_dir(watch_dir, settings):
        try:
            file_path.unlink(missing_ok=True)
            return True
        except OSError:
            logger.warning("Quelldatei konnte nicht entfernt werden: %s", file_path, exc_info=True)
            return False
    if _move_into(file_path, watch_dir, PROCESSED_DIRNAME):
        logger.info("Original verschoben nach %s: %s", PROCESSED_DIRNAME, file_path.name)
        return True
    return False


async def _import_file(
    file_path: Path, file_size: int, watch_dir: Path, session_factory, settings: Settings,
    access: _Access = LOCAL,
) -> str:
    """Gemeinsamer Import-Pfad fuer Ereignisse und Einlesen des Ordners.

    Haelt waehrend des ganzen Imports eine Lease (Netzwerk): zwischen Commit
    und dem Wegraeumen des Originals wird die Verbindung nicht getrennt.
    """
    original_name = file_path.name
    try:
        task_lease = access.open()
    except network_share.ShareError:
        logger.info("Keine Verbindung zum Netzwerk-Watch-Ordner – %s bleibt liegen", original_name)
        return SKIPPED
    try:
        # Erst hier (nach Settle-Delay/Stabilitaets-Check) pruefen: die Datei kann
        # zwischen Event und Import gegen einen Link getauscht worden sein (FH-04).
        if await access.run(_is_link, file_path):
            logger.warning("Verlinkte Datei im Watch-Ordner ignoriert: %s", file_path)
            return SKIPPED
        try:
            async with session_factory() as session:
                job = await process_upload(
                    file_path=file_path,
                    original_name=original_name,
                    file_size=file_size,
                    source=JobSource.WATCH_FOLDER,
                    settings=settings,
                    db=session,
                    io=access.run,
                )
                try:
                    await session.commit()
                except Exception:
                    discard_upload(job)
                    raise
            finished = await access.run(_finish_original, file_path, watch_dir, settings)
            logger.info("Watch-Ordner-Datei eingereicht: %s", original_name)
            return IMPORTED if finished else IMPORTED_LEFT

        except (SourceUnavailableError, network_share.ShareError) as e:
            # R3: Ein voruebergehender Lesefehler (SMB-Aussetzer) ist kein
            # ungueltiges Dokument — die Datei bleibt fuer den naechsten Versuch.
            logger.warning("Watch-Ordner-Datei %s vorübergehend nicht lesbar, bleibt liegen: %s", original_name, e)
            return SKIPPED

        except FileValidationError as e:
            logger.warning("Datei abgelehnt: %s - %s", original_name, e.message)
            await access.run(_move_to_rejected, file_path, watch_dir)
            return REJECTED

        except Exception:
            logger.exception("Fehler bei Verarbeitung von Watch-Ordner-Datei: %s", original_name)
            await access.run(_move_to_rejected, file_path, watch_dir)
            return REJECTED
    finally:
        task_lease.close()


class _WatchHandler(FileSystemEventHandler):
    """Reagiert auf neue, umbenannte und geaenderte Dateien im Watch-Ordner.

    Auch das Einlesen vorhandener Dateien laeuft ueber den Handler: gleiche
    Stabilitaetspruefung, keine Doppelimporte, und ``drain`` wartet auf alles.
    """

    def __init__(
        self, settings: Settings, session_factory, loop: asyncio.AbstractEventLoop, watch_dir: Path,
        access: _Access = LOCAL,
    ):
        super().__init__()
        self.settings = settings
        self.session_factory = session_factory
        self.loop = loop
        self.watch_dir = watch_dir
        self.access = access
        # Pfade in Bearbeitung (nur im Event-Loop gelesen/geschrieben) —
        # created + moved bzw. doppelte Events loesen keinen Doppelimport aus.
        self._in_flight: set[str] = set()
        # Laufende Import-Tasks (drain wartet darauf).
        self._tasks: set[asyncio.Task] = set()
        # Importiert, aber das Original liess sich nicht wegraeumen: nicht
        # erneut importieren, solange sich die Datei nicht aendert.
        self._left: dict[str, tuple[int, int]] = {}

    def _schedule(self, file_path: Path) -> None:
        if not _same_dir(file_path.parent, self.watch_dir):
            return  # Unterordner (rejected/, verarbeitet/) oder ausserhalb
        if not _is_candidate(file_path, self.settings):
            return
        self.loop.call_soon_threadsafe(self._start_task, file_path)

    def _start_task(self, file_path: Path) -> None:
        key = str(file_path)
        if key in self._in_flight:
            return
        self._in_flight.add(key)
        logger.info("Neue Datei erkannt: %s", file_path.name)
        task = self.loop.create_task(self._run(file_path, key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def schedule_existing(self, files: list[Path]) -> None:
        """Vorhandene Dateien einplanen (im Event-Loop aufrufen)."""
        for f in files:
            self._start_task(f)

    async def _run(self, file_path: Path, key: str) -> None:
        try:
            await self._process_when_stable(file_path)
        finally:
            self._in_flight.discard(key)

    async def drain(self, timeout: float | None) -> int:
        """Wartet auf laufende Importe. Sie werden danach NICHT abgebrochen
        (ein abgebrochener Import hinterliesse halbe Zustaende, R6) — jeder
        haelt seine Verbindung selbst. Liefert die Zahl der noch laufenden."""
        tasks = set(self._tasks)
        if not tasks:
            return 0
        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning("%d Watch-Ordner-Import(e) laufen beim Trennen noch weiter", len(pending))
        return len(pending)

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

    def on_modified(self, event: FileModifiedEvent) -> None:
        # Eine beim ersten Versuch noch wachsende Datei kommt so erneut an (R5).
        if event.is_directory:
            return
        self._schedule(Path(event.src_path))

    async def _handle_new_file(self, file_path: Path) -> None:
        """Einzelne Datei verarbeiten (Tests/Kompatibilitaet)."""
        key = str(file_path)
        if key in self._in_flight:
            return
        self._in_flight.add(key)
        await self._run(file_path, key)

    async def _signature(self, file_path: Path) -> tuple[int, int] | None:
        try:
            return await self.access.run(_file_signature, file_path)
        except (OSError, network_share.ShareError):
            return None

    async def _process_when_stable(self, file_path: Path) -> None:
        await asyncio.sleep(_SETTLE_DELAY)
        key = str(file_path)
        last = await self._signature(file_path)
        if last is None:
            logger.debug("Datei nicht (mehr) vorhanden: %s", file_path)
            return
        if self._left.get(key) == last:
            return  # schon importiert, Original liegt unveraendert noch da
        quiet = 0
        for _ in range(_STABILITY_MAX_POLLS):
            await asyncio.sleep(_STABILITY_POLL_INTERVAL)
            current = await self._signature(file_path)
            if current is None:
                logger.warning("Datei wurde während Stabilitäts-Check entfernt: %s", file_path)
                return
            if current != last or current[0] <= 0:
                last, quiet = current, 0
                continue
            quiet += 1
            if quiet < _STABILITY_QUIET_POLLS:
                continue
            try:
                locked = await self.access.run(_is_locked, file_path)
            except (OSError, network_share.ShareError):
                return
            if not locked:
                break
            quiet = 0  # noch geoeffnet (Scanner schreibt die naechste Seite)
        else:
            logger.warning(
                "Datei %s wird noch geschrieben (nach %.0f s) — bleibt liegen, neuer Versuch später",
                file_path, _STABILITY_MAX_POLLS * _STABILITY_POLL_INTERVAL,
            )
            return
        outcome = await _import_file(
            file_path, last[0], self.watch_dir, self.session_factory, self.settings, access=self.access,
        )
        if outcome == IMPORTED_LEFT:
            self._left[key] = last
        else:
            self._left.pop(key, None)


def _list_candidates(watch_dir: Path, settings: Settings) -> list[Path]:
    return [
        f for f in watch_dir.iterdir()
        if not _is_link(f) and f.is_file() and _is_candidate(f, settings)
    ]


async def _scan_existing_files(
    session_factory, settings: Settings, watch_dir: Path,
    handler: "_WatchHandler | None" = None, access: _Access = LOCAL,
) -> None:
    """Vorhandene Dateien im Watch-Ordner einlesen.

    Mit ``handler`` (laufende Ueberwachung): Dateien nur einplanen — gleiche
    Stabilitaetspruefung und Doppelimport-Schutz wie fuer Ereignisse. Ohne:
    eigener Handler, wartet bis alles verarbeitet ist.
    """
    # M5: Verzeichnis-Walk + stat() sind blockierend — im Thread ausfuehren.
    files = await access.run(_list_candidates, watch_dir, settings)
    if files:
        logger.info("Watch-Ordner einlesen: %d Datei(en) gefunden", len(files))
    if handler is not None:
        handler.schedule_existing(files)
        return
    if not files:
        return
    own = _WatchHandler(settings, session_factory, asyncio.get_running_loop(), watch_dir, access)
    own.schedule_existing(files)
    await own.drain(None)


async def resolve_watch_dir(session_factory, settings: Settings) -> Path:
    """Effektiver Watch-Ordner: DB-Einstellung (UI), Fallback settings.WATCH_DIR."""
    return (await resolve_watch_target(session_factory, settings)).path


@dataclass(frozen=True)
class WatchTarget:
    path: Path
    network: bool
    config: network_folder_service.FolderConfig
    poll_interval: int


def clamp_poll_interval(value) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_POLL_INTERVAL
    return max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, seconds))


async def resolve_watch_target(session_factory, settings: Settings) -> WatchTarget:
    """Watch-Ordner samt Zugangsdaten-Konfiguration und Pruefintervall."""
    from app.services.settings_service import get_db_setting

    async with session_factory() as session:
        config = await network_folder_service.load_folder_config(session, network_folder_service.WATCH, settings)
        interval = await get_db_setting(session, POLL_INTERVAL_KEY, "")
    poll = clamp_poll_interval(interval or settings.WATCH_POLL_INTERVAL)
    return WatchTarget(path=Path(config.path), network=config.network, config=config, poll_interval=poll)


def _guarded_listdir(target: str):
    """Verzeichnisliste fuer den Polling-Observer eines Netzwerkordners: vor
    jedem Durchlauf muss der eigene Verbindungseintrag noch bestehen — sonst
    beendet sich der Observer, statt mit dem Computerkonto weiterzulesen."""
    def listdir(path):
        try:
            network_share.connections.check_connected(target)
        except network_share.ShareError as exc:
            raise OSError(exc.code, str(exc)) from None
        with os.scandir(path) as it:
            return list(it)
    return listdir


def _make_observer(polling: bool, interval: int, listdir=None):
    if not polling:
        return Observer()
    # lstat statt stat: Verknuepfungen im Ordner wird nicht gefolgt (NET-SEC-07).
    return PollingObserverVFS(os.lstat, listdir or os.scandir, polling_interval=interval)


def _stop_observer(observer) -> bool:
    """Stoppt den Observer; True, wenn alle seine Threads beendet sind."""
    observer.stop()
    try:
        observer.join(timeout=_OBSERVER_JOIN_TIMEOUT)
    except RuntimeError:  # nie gestartet (start() schlug fehl)
        pass
    return not observer.is_alive() and not any(e.is_alive() for e in list(observer.emitters))


def _observer_alive(observer) -> bool:
    return observer.is_alive() and all(e.is_alive() for e in observer.emitters)


def _release_after_join(observer, lease: network_share.Lease) -> None:
    """Lease erst freigeben, wenn der (haengende) Observer wirklich beendet ist."""
    def _wait() -> None:
        try:
            observer.join()
        except RuntimeError:
            pass
        for emitter in list(observer.emitters):
            emitter.join()
        lease.close()
        logger.info("Watch-Observer beendet – Verbindung freigegeben")
    threading.Thread(target=_wait, name="zw-observer-release", daemon=True).start()


def _probe_dir(path: Path) -> None:
    """Erreichbarkeitsprobe (blockierend): Verzeichnis lesen."""
    with os.scandir(path) as it:
        next(it, None)


async def _probe(path: Path, timeout: float, access: _Access = LOCAL) -> None:
    try:
        await asyncio.wait_for(access.run(_probe_dir, path), timeout)
    except TimeoutError:
        raise network_share.ShareError(network_share.ERROR_SEM_TIMEOUT) from None
    except FileNotFoundError:
        raise network_share.ShareError(
            0, message="Netzwerkordner: Der Ordner existiert auf der Freigabe nicht (mehr).",
        ) from None
    except OSError as exc:
        code = getattr(exc, "winerror", None) or network_share.ERROR_NETNAME_DELETED
        raise network_share.ShareError(code) from None


def _check_local_dir(watch_dir: Path, create: bool) -> None:
    """Lokaler Ordner: keine Verknuepfung auf eine Freigabe (NET-SEC-07,
    auch nachtraeglich angelegt), vorhanden bzw. anlegbar."""
    link = network_share.find_remote_link(str(watch_dir))
    if link:
        raise OSError(0, network_share.remote_link_message(link))
    if create:
        watch_dir.mkdir(parents=True, exist_ok=True)
    elif not watch_dir.is_dir():
        raise FileNotFoundError(2, "Der Ordner ist nicht mehr vorhanden.")


def next_backoff(current: float) -> float:
    return min(current * 2, BACKOFF_MAX)


class _Session:
    """Ein Ueberwachungs-Durchlauf: (Verbinden,) Observer, Handler, Pruefungen.

    Reihenfolge beim Start (R4): erst der Observer (Ausgangszustand), dann
    das Einlesen der vorhandenen Dateien — eine waehrenddessen ankommende
    Datei loest ein Ereignis aus statt im Ausgangszustand zu verschwinden.
    Beim Beenden (NET-SEC-02): Observer stoppen, Importe abwarten, Verbindung
    freigeben — getrennt wird erst nach dem letzten laufenden Zugriff.
    """

    def __init__(self, session_factory, settings: Settings, target: WatchTarget, *, network: bool, polling: bool):
        self.session_factory = session_factory
        self.settings = settings
        self.target = target
        self.network = network
        self.polling = polling
        self.access = _Access(WATCH) if network else LOCAL
        self.watch_dir = target.path
        self.loop = asyncio.get_running_loop()
        self.handler: _WatchHandler | None = None
        self.observer = None
        self.observer_lease: network_share.Lease = network_share.NO_LEASE
        self.acquired = False
        self.last_rescan = 0.0
        self._stop_task: asyncio.Future | None = None

    async def start(self) -> None:
        if self.network:
            config = self.target.config
            cred = network_folder_service.credential_for(config, self.settings)
            root = network_share.parse_unc(config.path).root
            self.acquired = True  # auch bei Fehlschlag freigeben (idempotent)
            await asyncio.to_thread(network_share.connections.acquire, WATCH, root, cred)
            self.watch_dir = Path(network_share.connections.access_path(WATCH, str(self.target.path)))
            await _probe(self.watch_dir, _PROBE_TIMEOUT, self.access)
        else:
            await asyncio.to_thread(_check_local_dir, self.watch_dir, True)
        self.handler = _WatchHandler(self.settings, self.session_factory, self.loop, self.watch_dir, self.access)
        listdir = _guarded_listdir(WATCH) if self.network else None
        observer = _make_observer(self.polling, self.target.poll_interval, listdir)
        observer.schedule(self.handler, str(self.watch_dir), recursive=False)
        self.observer_lease = self.access.open()  # haelt die Verbindung, solange der Observer lebt
        self.observer = observer
        # start() nimmt den Ausgangszustand synchron auf (liest den Ordner) — im Thread.
        await self.access.run(observer.start)
        await _scan_existing_files(
            self.session_factory, self.settings, self.watch_dir, handler=self.handler, access=self.access,
        )
        self.last_rescan = self.loop.time()

    async def check(self) -> None:
        if self.network:
            await asyncio.to_thread(network_share.connections.check_connected, WATCH)
            await _probe(self.watch_dir, _PROBE_TIMEOUT, self.access)
        else:
            await asyncio.to_thread(_check_local_dir, self.watch_dir, False)
        if not _observer_alive(self.observer):
            if self.network:
                raise network_share.ShareError(network_share.ERROR_NETNAME_DELETED)
            raise OSError(0, "Die Ordnerüberwachung wurde unterbrochen.")
        if self.loop.time() - self.last_rescan >= _RESCAN_INTERVAL:
            self.last_rescan = self.loop.time()
            await _scan_existing_files(
                self.session_factory, self.settings, self.watch_dir, handler=self.handler, access=self.access,
            )

    async def _stop(self) -> None:
        observer, self.observer = self.observer, None
        if observer is not None:
            stopped = await asyncio.to_thread(_stop_observer, observer)
            if stopped:
                self.observer_lease.close()
            else:
                logger.warning("Watch-Observer reagiert nicht – Verbindung wird erst nach seinem Ende freigegeben")
                _release_after_join(observer, self.observer_lease)
        else:
            self.observer_lease.close()
        if self.handler is not None:
            await self.handler.drain(_DRAIN_TIMEOUT)
        if self.network and self.acquired:
            await asyncio.to_thread(network_share.connections.release, WATCH)

    async def stop(self) -> None:
        if self._stop_task is None:
            self._stop_task = asyncio.ensure_future(self._stop())
        await asyncio.shield(self._stop_task)


async def run_watch_folder(
    session_factory,
    settings: Settings,
) -> None:
    """Startet die Watch-Ordner-Ueberwachung. Liest den Pfad aus der DB (Fallback: .env).

    Lokale Ordner: Dateisystem-Ereignisse (bzw. Polling mit WATCH_FORCE_POLLING).
    Netzwerkordner (``\\\\server\\freigabe``): eigene Verbindung mit den
    gespeicherten Zugangsdaten, Polling und Wiederverbinden mit Backoff.
    """
    target = await resolve_watch_target(session_factory, settings)
    legacy_unc = target.network and not (target.config.user or target.config.token)
    if legacy_unc and settings.ALLOW_NETWORK_FOLDERS:
        # Experten-Override (bisheriges Verhalten): UNC ohne eigene Zugangsdaten,
        # Anmeldung mit dem Computerkonto — aber per Polling statt SMB-Ereignissen.
        await _run_local_watch(session_factory, settings, target, polling=True, mode="netzwerk")
    elif target.network:
        await _run_network_watch(session_factory, settings, target)
    else:
        await _run_local_watch(session_factory, settings, target)


async def _run_local_watch(
    session_factory, settings: Settings, target: WatchTarget, polling: bool | None = None, mode: str = "lokal",
) -> None:
    polling = settings.WATCH_FORCE_POLLING if polling is None else polling
    await _watch_loop(session_factory, settings, target, network=False, polling=polling, mode=mode)


async def _run_network_watch(session_factory, settings: Settings, target: WatchTarget) -> None:
    r"""Zustandsmaschine VERBINDE -> OK -> GETRENNT (Backoff) -> VERBINDE ...

    FH-01: Kein Zugriff auf den UNC-Pfad ohne eigene Verbindung. Fehlt der
    Verbindungseintrag, wird nicht unter laufenden Zugriffen neu verbunden,
    sondern die Sitzung beendet und nach dem Backoff neu aufgebaut.
    """
    await _watch_loop(session_factory, settings, target, network=True, polling=True, mode="netzwerk")


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, (network_share.ShareError, network_share.UncPathError)):
        return str(exc)
    code = getattr(exc, "winerror", None)
    if code:
        return network_share.message_for(code)
    text = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
    return f"Ordner nicht verfügbar ({text})."


async def _watch_loop(
    session_factory, settings: Settings, target: WatchTarget, *, network: bool, polling: bool, mode: str,
) -> None:
    """Gemeinsame Schleife fuer lokale und Netzwerk-Ordner (R9): faellt der
    Observer aus (z.B. Polling nach einem Lesefehler) oder ist der Ordner nicht
    verfuegbar, wird die Sitzung beendet, der Grund im Status gemeldet und mit
    Backoff neu gestartet — die Ueberwachung stirbt nie still."""
    status = network_folder_service.status
    config = target.config
    base = dict(
        mode=mode,
        path=config.path if network else str(target.path),
        user=config.user if network else None,
        poll_interval=target.poll_interval if polling else None,
    )
    check_interval = target.poll_interval if network else _LOCAL_CHECK_INTERVAL
    failed_state = "getrennt" if network else "fehler"
    backoff = BACKOFF_START
    last_logged: tuple[str | None, float] = (None, 0.0)
    session: _Session | None = None
    try:
        while True:
            session = _Session(session_factory, settings, target, network=network, polling=polling)
            try:
                if network:
                    status.update(WATCH, state="verbinde", next_retry_at=None, **base)
                await session.start()
                status.update(WATCH, state="ok", **base)
                if last_logged[0] is not None:
                    logger.info("Watch-Ordner wieder erreichbar: %s", base["path"])
                else:
                    logger.info(
                        "Watch-Ordner wird überwacht%s: %s",
                        f" (alle {target.poll_interval} s)" if polling else "", base["path"],
                    )
                last_logged = (None, 0.0)
                backoff = BACKOFF_START
                while True:
                    await asyncio.sleep(check_interval)
                    await session.check()
            except asyncio.CancelledError:
                raise
            except (network_share.ShareError, network_share.UncPathError, OSError) as exc:
                reason = _failure_reason(exc)
                await session.stop()
                session = None
                retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                status.update(WATCH, state=failed_state, reason=reason, next_retry_at=retry_at.isoformat(), **base)
                now = time.monotonic()
                if reason != last_logged[0] or now - last_logged[1] >= _LOG_REPEAT_SECONDS:
                    logger.warning(
                        "Watch-Ordner %s nicht erreichbar: %s — neuer Versuch in %d s", base["path"], reason, backoff,
                    )
                    last_logged = (reason, now)
                await asyncio.sleep(backoff)
                backoff = next_backoff(backoff)
    except asyncio.CancelledError:
        logger.info("Watch-Ordner-Überwachung wird beendet")
        if session is not None:
            await session.stop()
        raise
