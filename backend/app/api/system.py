"""System-Health und Backup-API."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.install_dir import native_install_dir
from app.core.version import read_app_version
from app.database import SQLITE_BUSY_TIMEOUT_MS, get_db, get_session_factory
from app.models.document import Document, DocumentStatus
from app.schemas.background_task import TaskResponse
from app.services.backup_service import (
    backup_dir,
    create_backup,
    get_system_info,
    list_backups,
)
from app.services.ollama_client import get_last_outcomes, ollama_health
from app.services.settings_service import get_db_setting, set_db_setting
from app.services.task_service import TaskContext, TaskError, start_task

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


class FolderSettings(BaseModel):
    watch_dir: str
    export_dir: str
    watch_dir_host: str = ""
    export_dir_host: str = ""
    restart_required: bool = False


class FolderSettingsResponse(FolderSettings):
    # "docker" | "native" | "dev" — das Frontend zeigt danach entweder die
    # Host-Mount-Felder (Docker) oder die Ordnerpfade direkt (Native/Dev).
    deployment_mode: str


# Liest generate-mounts.ps1 (start.bat) im Docker-Betrieb und erzeugt daraus
# die Bind-Mounts in docker-compose.override.yml. Liegt im Datenordner
# (Container: /app/data = Host: <install>/data).
HOST_MOUNTS_FILENAME = ".host-mounts.json"
# Container-Pfade, unter denen die Host-Mounts im Docker-Betrieb erscheinen.
DOCKER_EXTERNAL_WATCH = "/app/external/watch"
DOCKER_EXTERNAL_EXPORT = "/app/external/export"


def host_mounts_file(settings: Settings) -> Path:
    return settings.data_dir / HOST_MOUNTS_FILENAME


def _write_host_mounts(settings: Settings, watch_host: str, export_host: str) -> None:
    """Schreibt die Host-Mount-Konfiguration nach <data>/.host-mounts.json."""
    mounts: dict = {}
    if watch_host:
        mounts["watch"] = watch_host
    if export_host:
        mounts["export"] = export_host
    path = host_mounts_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mounts:
        path.write_text(json.dumps(mounts, indent=2), encoding="utf-8")
    elif path.exists():
        path.unlink()


_FORBIDDEN_PATH_PREFIXES = (
    # Windows (case-insensitive Vergleich; lower() weiter unten)
    "c:\\windows", "c:/windows",
    "c:\\program files", "c:/program files",
    "c:\\programdata", "c:/programdata",
    # Linux/macOS System-Verzeichnisse
    "/etc", "/proc", "/sys", "/root", "/boot", "/dev",
    "/var/log", "/var/run", "/var/lib/docker",
    "/usr/bin", "/usr/sbin", "/sbin", "/bin",
)


def _normalize_path_input(value: str | None) -> str:
    """Leerraum und umschliessende Anfuehrungszeichen entfernen.

    Windows-Explorer "Als Pfad kopieren" liefert "C:\\..." in Anfuehrungszeichen;
    Anfuehrungszeichen sind in Windows-Pfaden ohnehin unzulaessig.
    """
    return (value or "").strip().strip("\"'").strip()


def _reject_dangerous(path_str: str, label: str) -> None:
    """Systemverzeichnisse und '..'-Segmente ablehnen (docs/reviews/SECURITY_AUDIT_v2.md N-003)."""
    if ".." in path_str.replace("\\", "/").split("/"):
        raise HTTPException(400, f"{label} enthält unerlaubte Pfad-Segmente: {path_str}")
    p = path_str.lower().replace("\\", "/").rstrip("/")
    for forbidden in _FORBIDDEN_PATH_PREFIXES:
        f = forbidden.lower().replace("\\", "/").rstrip("/")
        if p == f or p.startswith(f + "/"):
            raise HTTPException(400, f"{label} darf nicht auf Systemverzeichnisse zeigen: {path_str}")


def _validate_host_path(value: str, label: str) -> str:
    """Docker: Host-Pfad fuer einen Bind-Mount pruefen; liefert ihn normalisiert.

    Der Host kann Windows oder Linux sein — daher beide Pfadformen. Ohne die
    Pruefung koennte jemand `C:\\Windows\\System32` in den Container mounten
    (N-003).
    """
    path_str = _normalize_path_input(value)
    if not path_str:
        return ""  # leer = deaktiviert
    _reject_dangerous(path_str, label)
    win = PureWindowsPath(path_str)
    if not (win.is_absolute() or path_str.startswith("/")):
        raise HTTPException(400, f"{label} muss ein absoluter Pfad sein: {path_str}")
    if (win.is_absolute() and str(win) == win.anchor) or path_str.rstrip("/") == "":
        raise HTTPException(400, f"{label} darf kein Laufwerks- oder Wurzelverzeichnis sein: {path_str}")
    return path_str


def _validate_local_dir(value: str, label: str, settings: Settings) -> str:
    """Pfad, den das Backend selbst liest/schreibt (Watch-/Export-Ordner).

    Muss absolut sein, darf weder System- noch Wurzelverzeichnis sein und
    nicht in die von der App verwalteten Ordner (Daten-, Archiv-, Upload-,
    Thumbnail-, Backup- und Programmordner) zeigen — der Watcher wuerde sonst
    die eigenen Ablagen einlesen bzw. der Export ins Archiv schreiben.
    """
    path_str = _normalize_path_input(value)
    _reject_dangerous(path_str, label)
    path = Path(path_str)
    if not path.is_absolute():
        raise HTTPException(400, f"{label} muss ein absoluter Pfad sein: {path_str}")
    resolved = path.resolve()
    _reject_dangerous(str(resolved), label)
    if resolved.parent == resolved:
        raise HTTPException(400, f"{label} darf kein Laufwerks- oder Wurzelverzeichnis sein: {path_str}")

    data_dir = settings.data_dir.resolve()
    if resolved == data_dir or data_dir.is_relative_to(resolved):
        raise HTTPException(400, f"{label} darf nicht der Datenordner oder einer seiner Elternordner sein: {path_str}")
    managed = [settings.ARCHIVE_DIR, settings.UPLOAD_DIR, settings.THUMBNAIL_DIR, str(data_dir / "backups")]
    install_dir = native_install_dir()
    if install_dir is not None:
        managed.append(str(install_dir))
    for managed_dir in managed:
        if resolved.is_relative_to(Path(managed_dir).resolve()):
            raise HTTPException(400, f"{label} darf nicht in einem Programm- oder Ablageordner liegen: {path_str}")
    return str(path)


def _effective_watch_dir(value: str, settings: Settings) -> str:
    """Leer oder der konfigurierte Standard -> app-verwalteter Standard-Watch-Ordner."""
    path_str = _normalize_path_input(value)
    if not path_str or path_str == settings.WATCH_DIR:
        return settings.WATCH_DIR
    return _validate_local_dir(path_str, "Watch-Ordner", settings)


def _effective_export_dir(value: str, settings: Settings) -> str:
    """Leer -> Export deaktiviert; der konfigurierte EXPORT_DIR bleibt zulaessig."""
    path_str = _normalize_path_input(value)
    if not path_str or path_str == settings.EXPORT_DIR:
        return path_str
    return _validate_local_dir(path_str, "Export-Ordner", settings)


@router.get("/system/settings", response_model=FolderSettingsResponse)
async def get_folder_settings(
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Liest die konfigurierten Ordner-Einstellungen."""
    docker = settings.deployment_mode == "docker"
    return FolderSettingsResponse(
        watch_dir=await get_db_setting(session, "watch_dir", settings.WATCH_DIR),
        export_dir=await get_db_setting(session, "export_dir", settings.EXPORT_DIR),
        # Host-Mounts gibt es nur im Docker-Betrieb.
        watch_dir_host=await get_db_setting(session, "watch_dir_host", "") if docker else "",
        export_dir_host=await get_db_setting(session, "export_dir_host", "") if docker else "",
        deployment_mode=settings.deployment_mode,
    )


@router.put("/system/settings", response_model=FolderSettingsResponse)
async def update_folder_settings(
    body: FolderSettings,
    request: Request,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Aktualisiert Watch-Ordner und Export-Ordner. Startet Watch-Task bei Pfadaenderung neu.

    Docker: die *_host-Felder sind Host-Ordner, die per Bind-Mount unter
    /app/external/* erscheinen (wirksam nach Neustart via start.bat).
    Native/Dev: das Backend greift direkt auf die Ordner zu — ein Host-Pfad
    (falls die UI ihn schickt) wird direkt als Ordnerpfad uebernommen.
    """
    mode = settings.deployment_mode
    old_watch_dir = await get_db_setting(session, "watch_dir", settings.WATCH_DIR)

    # Erst ALLES validieren, dann speichern.
    if mode == "docker":
        watch_host = _validate_host_path(body.watch_dir_host, "Watch-Ordner")
        export_host = _validate_host_path(body.export_dir_host, "Export-Ordner")
        if watch_host:
            watch_dir = DOCKER_EXTERNAL_WATCH
        elif _normalize_path_input(body.watch_dir) == DOCKER_EXTERNAL_WATCH:
            watch_dir = settings.WATCH_DIR  # Host-Pfad geleert -> Standard
        else:
            watch_dir = _effective_watch_dir(body.watch_dir, settings)
        if export_host:
            export_dir = DOCKER_EXTERNAL_EXPORT
        elif _normalize_path_input(body.export_dir) == DOCKER_EXTERNAL_EXPORT:
            export_dir = ""  # Host-Pfad geleert -> Export aus
        else:
            export_dir = _effective_export_dir(body.export_dir, settings)
        old_watch_host = await get_db_setting(session, "watch_dir_host", "")
        old_export_host = await get_db_setting(session, "export_dir_host", "")
        restart_required = watch_host != old_watch_host or export_host != old_export_host
    else:
        watch_host = export_host = ""
        watch_value = _normalize_path_input(body.watch_dir_host) or _normalize_path_input(body.watch_dir)
        export_value = _normalize_path_input(body.export_dir_host) or _normalize_path_input(body.export_dir)
        # Altlast: fruehere Versionen haben auch hier die Docker-Containerpfade
        # gespeichert — die zeigen ausserhalb von Docker ins Leere.
        if watch_value == DOCKER_EXTERNAL_WATCH:
            watch_value = ""
        if export_value == DOCKER_EXTERNAL_EXPORT:
            export_value = ""
        watch_dir = _effective_watch_dir(watch_value, settings)
        export_dir = _effective_export_dir(export_value, settings)
        restart_required = False
        # Ordner muessen existieren bzw. anlegbar sein — sonst wuerde die UI
        # Erfolg melden, waehrend der Watcher ins Leere schaut.
        for label, dir_path in (("Watch-Ordner", watch_dir), ("Export-Ordner", export_dir)):
            if not dir_path:
                continue
            try:
                Path(dir_path).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(400, f"{label} kann nicht angelegt werden: {dir_path} ({exc.strerror or exc})")

    await set_db_setting(session, "watch_dir", watch_dir)
    await set_db_setting(session, "export_dir", export_dir)
    await set_db_setting(session, "watch_dir_host", watch_host)
    await set_db_setting(session, "export_dir_host", export_host)
    await session.commit()

    if mode == "docker":
        # Die Bind-Mounts entstehen erst beim Neustart (start.bat ->
        # generate-mounts.ps1); /app/external/* wird daher hier nicht angelegt.
        try:
            _write_host_mounts(settings, watch_host, export_host)
        except OSError:
            logger.warning("Konnte %s nicht schreiben", host_mounts_file(settings), exc_info=True)

    # Watch-Task neu starten wenn sich der Pfad geaendert hat
    if watch_dir != old_watch_dir:
        await _restart_watch_task(request.app, settings)
        logger.info("Watch-Ordner geaendert: %s -> %s", old_watch_dir, watch_dir)

    return FolderSettingsResponse(
        watch_dir=watch_dir,
        export_dir=export_dir,
        watch_dir_host=watch_host,
        export_dir_host=export_host,
        restart_required=restart_required,
        deployment_mode=mode,
    )


async def _restart_watch_task(app, settings: Settings) -> None:
    """Stoppt den laufenden Watch-Task und startet ihn mit dem neuen Pfad neu."""
    old_task = getattr(app.state, "watch_task", None)
    if old_task and not old_task.done():
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass

    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        logger.warning("session_factory nicht in app.state – Watch-Task kann nicht neu gestartet werden")
        return

    from app.services.watch_folder_service import run_watch_folder
    new_task = asyncio.create_task(run_watch_folder(session_factory, settings))
    app.state.watch_task = new_task
    logger.info("Watch-Ordner-Task neu gestartet")


# Speicher-Statistik (rglob+stat ueber das ganze Archiv, M-20) nur alle 60 s neu
# berechnen — die Einstellungsseite pollt /system/health alle 10 s.
_SYSTEM_INFO_TTL = 60.0
_system_info_cache: dict = {"key": None, "at": 0.0, "data": None}


async def _cached_system_info(settings: Settings) -> dict:
    key = (settings.DATABASE_URL, settings.ARCHIVE_DIR, settings.UPLOAD_DIR)
    now = time.monotonic()
    if (
        _system_info_cache["data"] is not None
        and _system_info_cache["key"] == key
        and now - _system_info_cache["at"] < _SYSTEM_INFO_TTL
    ):
        return _system_info_cache["data"]
    data = await asyncio.to_thread(get_system_info, settings)
    _system_info_cache.update(key=key, at=now, data=data)
    return data


def _reset_system_info_cache() -> None:
    """Nur fuer Tests."""
    _system_info_cache.update(key=None, at=0.0, data=None)


def _overall_status(components: dict) -> str:
    # Gleiches Vokabular wie /api/health: ok | warning | error.
    statuses = {c.get("status") for c in components.values()}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ok"


@router.get("/system/health")
async def system_health(
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Detaillierter System-Gesundheitscheck.

    Status-Vokabular wie /api/health: ``ok`` | ``warning`` (Funktion
    eingeschraenkt, z.B. Ollama/ChromaDB nicht erreichbar) | ``error``.
    """
    components = {}

    # Backend
    components["backend"] = {"status": "ok"}

    # Datenbank — S-LOW5: keine internen Exception-Details nach aussen.
    try:
        await session.execute(text("SELECT 1"))
        components["database"] = {"status": "ok"}
    except Exception:
        logger.exception("System-Health: Datenbank nicht erreichbar")
        components["database"] = {"status": "error", "message": "Datenbankfehler"}

    # Ollama: gleicher (gecachter) Zustand wie /api/health, dazu die
    # benoetigten Modelle mit Verfuegbarkeit und der letzte echte Aufruf.
    ollama = await ollama_health(settings)
    components["ollama"] = {
        "status": ollama.status,
        "message": ollama.message,
        "configured_models": ollama.configured_models(settings),
        "missing": ollama.probe.missing,
        "last_calls": get_last_outcomes(),
    }

    # ChromaDB (H1: Native/embedded hat keinen HTTP-Service — sonst meldet der
    # Health-Check dauerhaft "nicht erreichbar", obwohl die Vektorsuche laeuft)
    from app.services.vectorize_service import get_collection_count
    chroma_unreachable = {"status": "warning", "message": "Nicht erreichbar"}
    try:
        reachable = True
        if settings.CHROMADB_MODE != "embedded":
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://{settings.CHROMADB_HOST}:{settings.CHROMADB_PORT}/api/v2/heartbeat")
            if resp.status_code != 200:
                reachable = False
                components["chromadb"] = {"status": "warning", "message": f"HTTP {resp.status_code}"}
        if reachable:
            # In-process bzw. nach erfolgreichem Heartbeat: Count (blockierend ->
            # to_thread). None heisst Fehler — nicht als "0 Vektoren" ausgeben.
            vec_count = await asyncio.to_thread(get_collection_count, settings)
            components["chromadb"] = (
                chroma_unreachable if vec_count is None else {"status": "ok", "vectors": vec_count}
            )
    except Exception:
        components["chromadb"] = chroma_unreachable

    sys_info = await _cached_system_info(settings)

    # Dokument-Statistiken
    doc_count_result = await session.execute(
        select(func.count()).select_from(Document).where(Document.status != DocumentStatus.DELETED)
    )
    doc_count = doc_count_result.scalar() or 0

    # Installationspfad (vom Installer in den Datenordner geschrieben; install.ps1
    # schreibt mit Windows PowerShell 5.1 "-Encoding UTF8" = mit BOM -> utf-8-sig)
    install_path_file = settings.data_dir / ".install-path"
    try:
        install_path = install_path_file.read_text(encoding="utf-8-sig").strip() if install_path_file.exists() else ""
    except OSError:
        install_path = ""

    result = {
        "status": _overall_status(components),
        "app_version": read_app_version(),
        "deployment_mode": settings.deployment_mode,
        "components": components,
        "statistics": {
            "total_documents": doc_count,
            **sys_info,
        },
    }
    if install_path:
        result["install_path"] = install_path
    return result


class UpdateCheckResponse(BaseModel):
    enabled: bool
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str | None = None
    published_at: str | None = None
    notes: str | None = None
    checked_at: str | None = None
    cached: bool = False
    error: str | None = None


class UpdateCheckSettings(BaseModel):
    enabled: bool


UPDATE_CHECK_SETTING_KEY = "update_check_enabled"


async def _update_check_enabled(session: AsyncSession, settings: Settings) -> bool:
    """DB-Einstellung hat Vorrang; faellt auf den Config-Default zurueck."""
    raw = await get_db_setting(session, UPDATE_CHECK_SETTING_KEY, "")
    if raw == "":
        return settings.UPDATE_CHECK_ENABLED
    return raw == "true"


@router.get("/system/update-check", response_model=UpdateCheckResponse)
async def get_update_check(
    force: bool = False,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Opt-in Update-Pruefung. Macht nur dann einen externen Aufruf, wenn der
    Nutzer die Pruefung aktiviert hat."""
    from app.services import update_service

    if not await _update_check_enabled(session, settings):
        return UpdateCheckResponse(
            enabled=False, current_version=update_service.read_local_version()
        )
    data = await update_service.check_for_update(settings.UPDATE_MANIFEST_URL, force=force)
    return UpdateCheckResponse(
        enabled=True, checked_at=datetime.now(timezone.utc).isoformat(), **data
    )


@router.put("/system/update-check", response_model=UpdateCheckResponse)
async def set_update_check(
    body: UpdateCheckSettings,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Update-Pruefung aktivieren/deaktivieren (opt-in). Bei Aktivierung wird
    sofort einmal geprueft."""
    from app.services import update_service

    await set_db_setting(session, UPDATE_CHECK_SETTING_KEY, "true" if body.enabled else "false")
    await session.commit()
    if not body.enabled:
        return UpdateCheckResponse(
            enabled=False, current_version=update_service.read_local_version()
        )
    data = await update_service.check_for_update(settings.UPDATE_MANIFEST_URL, force=True)
    return UpdateCheckResponse(
        enabled=True, checked_at=datetime.now(timezone.utc).isoformat(), **data
    )


@router.post("/system/backup", status_code=202, response_model=TaskResponse)
async def create_backup_endpoint(
    full: bool = False,
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> TaskResponse:
    """Startet ein Backup als Hintergrund-Vorgang (HTTP 202 + Polling ueber
    GET /api/tasks/{id}). Ein Voll-Backup packt das ganze Archiv und kann
    Minuten dauern; es laeuft immer nur ein Backup gleichzeitig."""

    async def work(ctx: TaskContext) -> dict:
        await ctx.report_progress(
            None, "Erstelle Vollbackup (Datenbank + Dokumente) …" if full else "Erstelle Datenbank-Backup …"
        )
        try:
            # T17: create_backup() laeuft rekursiv ueber das Archiv und erzeugt
            # die ZIP — blockierender I/O, daher im Thread.
            path = Path(await asyncio.to_thread(create_backup, settings, full))
        except FileNotFoundError:
            logger.exception("Backup fehlgeschlagen: Datenbankdatei fehlt")
            raise TaskError("Backup fehlgeschlagen: Die Datenbankdatei wurde nicht gefunden.")
        except Exception:
            logger.exception("Backup fehlgeschlagen")
            raise TaskError("Backup fehlgeschlagen. Details stehen im Server-Log.")
        return {
            "filename": path.name,
            "is_full": full,
            "size_bytes": path.stat().st_size,
            "message": "Vollbackup erstellt" if full else "Backup erstellt",
        }

    task = await start_task(
        session_factory, "backup", work, key="global",
        conflict_message="Es läuft bereits ein Backup.",
    )
    return TaskResponse.model_validate(task)


@router.get("/system/backups")
async def list_backups_endpoint(settings: Settings = Depends(get_settings)):
    """Listet vorhandene Backups (neueste zuerst)."""
    return {"backups": await asyncio.to_thread(list_backups, settings)}


@router.get("/system/backups/{filename}")
async def download_backup(
    filename: str,
    settings: Settings = Depends(get_settings),
):
    """Backup-Datei herunterladen."""
    directory = backup_dir(settings)
    file_path = directory / filename
    # Path-Traversal-Schutz
    if not file_path.resolve().is_relative_to(directory.resolve()):
        raise HTTPException(400, "Ungültiger Dateiname")
    if not file_path.name.startswith("backup_") or not file_path.name.endswith(".zip") or not file_path.is_file():
        raise HTTPException(404, "Backup nicht gefunden")
    return FileResponse(file_path, filename=file_path.name, media_type="application/zip")


@router.post("/system/maintenance/optimize-db")
async def optimize_db(settings: Settings = Depends(get_settings)):
    """Datenbank optimieren (VACUUM).

    VACUUM darf nicht in einer aktiven Transaktion laufen und blockiert alle
    Writes. Wir nutzen daher eine fresh sqlite3-Connection ausserhalb des
    SQLAlchemy-Pools und rufen sie in to_thread, um den Event-Loop nicht zu
    blockieren.
    """
    import sqlite3

    try:
        db_path = settings.sqlite_db_path
    except ValueError:
        raise HTTPException(500, "DB-Pfad konnte nicht ermittelt werden")

    def _vacuum() -> None:
        # autocommit; wartet wie die App-Verbindungen auf fremde Schreibsperren
        conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    try:
        await asyncio.to_thread(_vacuum)
        return {"message": "Datenbank optimiert"}
    except Exception:
        logger.exception("DB-Optimierung fehlgeschlagen")
        raise HTTPException(500, "Optimierung fehlgeschlagen")


@router.post("/system/maintenance/rebuild-index")
async def rebuild_index(session: AsyncSession = Depends(get_db, scope="function")):
    """FTS5-Suchindex neu aufbauen."""
    try:
        from app.services.search_service import rebuild_fts_index
        count = await rebuild_fts_index(session)
        return {"message": f"Index für {count} Dokumente neu aufgebaut"}
    except Exception:
        logger.exception("Index-Rebuild fehlgeschlagen")
        raise HTTPException(500, "Index-Rebuild fehlgeschlagen")


@router.post("/system/maintenance/rebuild-vectors", status_code=202, response_model=TaskResponse)
async def rebuild_vectors(
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> TaskResponse:
    """Vektor-Index fuer alle Dokumente neu aufbauen — Hintergrund-Vorgang
    ``rebuild_vectors`` (HTTP 202 + Polling ueber GET /api/tasks/{id}); 409,
    solange schon ein Rebuild (auch die initiale Vektorisierung) laeuft."""
    from app.services.vectorize_service import start_vector_rebuild

    task = await start_vector_rebuild(session_factory, settings)
    return TaskResponse.model_validate(task)
