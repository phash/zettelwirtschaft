"""Gemeinsame Infrastruktur fuer langlaufende Hintergrund-Vorgaenge.

Re-Analyse, manueller E-Mail-Abruf und Voll-Backup dauern laenger als ein
HTTP-Request sinnvoll offen bleiben kann (Ollama, IMAP, grosse ZIPs). Die
Endpoints starten den Vorgang deshalb mit :func:`start_task`, antworten sofort
mit HTTP 202 + Task-Objekt, und das Frontend pollt ``GET /api/tasks/{id}``
(Composable ``useBackgroundTask``).

Regeln fuer ``work``-Funktionen:

- Nie die Request-Session weiterverwenden — die ist nach der 202-Antwort
  geschlossen. Eigene Sessions ueber ``ctx.session_factory`` oeffnen.
- Keine Schreib-Transaktion ueber langsame Arbeit (OCR/LLM/IMAP) offen halten
  (SQLite-Writer-Lock). Vor ``ctx.report_progress`` committen — Progress wird
  in einer eigenen kurzen Transaktion geschrieben.
- Erwartete Fehler als :class:`TaskError` (deutsche Meldung fuer die UI)
  werfen; ``HTTPException`` wird ebenfalls mit ihrem ``detail`` uebernommen.
  Alles andere landet als generisches "Interner Fehler (<Typ>)" im Task —
  der Exception-Text (Pfade, SQL, Parameter) nur im Server-Log (SEC-DATA-05).
- Der Rueckgabewert wird per ``jsonable_encoder`` als ``result`` gespeichert.

Alles laeuft in-process: nach einem Neustart setzt
:func:`recover_interrupted_tasks` verwaiste PENDING/RUNNING-Eintraege auf FAILED.
"""

import asyncio
import logging
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.public_errors import internal_error_message
from app.models.background_task import ACTIVE_TASK_STATUSES, BackgroundTask, TaskStatus

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "Neustart während Ausführung — Vorgang wurde abgebrochen."
SHUTDOWN_MESSAGE = "Server wurde während der Ausführung beendet — Vorgang abgebrochen."
DEFAULT_CONFLICT_MESSAGE = "Dieser Vorgang läuft bereits."
FINISHED_RETENTION_DAYS = 30
_MAX_ERROR_LEN = 2000
_MAX_PROGRESS_TEXT_LEN = 500

SessionFactory = async_sessionmaker[AsyncSession]

# Starke Referenzen auf laufende asyncio-Tasks (sonst kann der GC sie
# einsammeln) — Key ist die BackgroundTask-ID.
_running: dict[str, asyncio.Task] = {}
# Serialisiert "aktiven Task suchen + anlegen", damit zwei parallele Requests
# nicht beide den Guard passieren. Der Partial Unique Index ist die zweite Linie.
# Ein Lock pro Event-Loop (asyncio.Lock bindet sich an den Loop der ersten
# Konkurrenz-Situation; Tests laufen mit einem Loop pro Testfunktion).
_start_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _start_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _start_locks.get(loop)
    if lock is None:
        lock = _start_locks[loop] = asyncio.Lock()
    return lock


class TaskError(Exception):
    """Erwarteter Fehler eines Vorgangs — ``message`` wird der UI angezeigt."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TaskConflictError(Exception):
    """Fuer (kind, key) laeuft bereits ein Vorgang.

    Ein globaler Exception-Handler (main.py) macht daraus HTTP 409 mit
    ``{"detail": message, "task": <TaskResponse des laufenden Vorgangs>}`` —
    das Frontend haengt sich dann an den laufenden Vorgang an.
    """

    def __init__(self, active_task: BackgroundTask, message: str = DEFAULT_CONFLICT_MESSAGE):
        super().__init__(message)
        self.active_task = active_task
        self.message = message


DEFAULT_LIMIT_MESSAGE = "Es laufen bereits zu viele Vorgänge dieser Art. Bitte warten, bis sie fertig sind."


class TaskLimitError(HTTPException):
    """AVAIL-06: zu viele aktive Vorgaenge dieser Art (``max_active``) -> HTTP 429."""

    def __init__(self, message: str = DEFAULT_LIMIT_MESSAGE):
        super().__init__(status_code=429, detail=message)


@dataclass(frozen=True)
class TaskContext:
    """Wird an die ``work``-Funktion uebergeben."""

    task_id: str
    kind: str
    key: str | None
    session_factory: SessionFactory

    async def report_progress(
        self, percent: int | None = None, text: str | None = None
    ) -> None:
        """Fortschritt speichern (eigene kurze Transaktion, best effort).

        Fehler (z.B. SQLite-Lock, weil ``work`` noch eine offene Schreib-
        Transaktion haelt) werden geloggt, nicht geworfen — Progress darf den
        eigentlichen Vorgang nie abbrechen.
        """
        values: dict[str, Any] = {}
        if percent is not None:
            values["progress_percent"] = max(0, min(100, int(percent)))
        if text is not None:
            values["progress_text"] = text[:_MAX_PROGRESS_TEXT_LEN]
        if not values:
            return
        try:
            async with self.session_factory() as session:
                await session.execute(
                    update(BackgroundTask)
                    .where(BackgroundTask.id == self.task_id)
                    .values(**values)
                )
                await session.commit()
        except Exception:
            logger.warning("Fortschritt fuer Task %s nicht gespeichert", self.task_id, exc_info=True)


TaskWork = Callable[[TaskContext], Awaitable[Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _find_active(session: AsyncSession, kind: str, key: str) -> BackgroundTask | None:
    result = await session.execute(
        select(BackgroundTask)
        .where(BackgroundTask.kind == kind)
        .where(BackgroundTask.key == key)
        .where(BackgroundTask.status.in_(ACTIVE_TASK_STATUSES))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_active_task(
    session_factory: SessionFactory, kind: str, key: str
) -> BackgroundTask | None:
    """Aktiver (PENDING/RUNNING) Vorgang fuer (kind, key) oder None."""
    async with session_factory() as session:
        return await _find_active(session, kind, key)


async def get_task(session_factory: SessionFactory, task_id: str) -> BackgroundTask | None:
    async with session_factory() as session:
        return await session.get(BackgroundTask, task_id)


async def start_task(
    session_factory: SessionFactory,
    kind: str,
    work: TaskWork,
    *,
    key: str | None = None,
    conflict_message: str = DEFAULT_CONFLICT_MESSAGE,
    max_active: int | None = None,
    limit_message: str = DEFAULT_LIMIT_MESSAGE,
) -> BackgroundTask:
    """Legt einen Vorgang an und startet ``work(ctx)`` im Event-Loop der App.

    Mit ``key`` ist hoechstens ein aktiver Vorgang pro (kind, key) erlaubt —
    sonst :class:`TaskConflictError`. Ohne ``key`` kein Guard. Fuer "nur ein
    Vorgang dieser Art gleichzeitig" einen festen Key verwenden (z.B. "global").
    ``max_active`` deckelt zusaetzlich die aktiven Vorgaenge dieses ``kind``
    ueber alle Keys (AVAIL-06) — sonst :class:`TaskLimitError` (HTTP 429).

    Gibt den frisch angelegten (PENDING) Datensatz zurueck; der Endpoint
    antwortet damit als ``TaskResponse`` mit Status 202.
    """
    async with _start_lock():
        async with session_factory() as session:
            if key is not None:
                active = await _find_active(session, kind, key)
                if active is not None:
                    raise TaskConflictError(active, conflict_message)
            if max_active is not None:
                active_count = await session.scalar(
                    select(func.count())
                    .select_from(BackgroundTask)
                    .where(BackgroundTask.kind == kind)
                    .where(BackgroundTask.status.in_(ACTIVE_TASK_STATUSES))
                )
                if (active_count or 0) >= max_active:
                    raise TaskLimitError(limit_message)
            task = BackgroundTask(kind=kind, key=key, status=TaskStatus.PENDING)
            session.add(task)
            try:
                await session.commit()
            except IntegrityError:
                # Partial Unique Index hat zugeschlagen (z.B. zweiter Prozess).
                await session.rollback()
                active = await _find_active(session, kind, key) if key is not None else None
                if active is None:
                    raise
                raise TaskConflictError(active, conflict_message) from None
            # Laden, solange die Session offen ist (robust auch bei expire_on_commit=True).
            await session.refresh(task)

        ctx = TaskContext(task_id=task.id, kind=kind, key=key, session_factory=session_factory)
        runner = asyncio.create_task(_run(ctx, work), name=f"background-task:{kind}:{task.id}")
        _running[task.id] = runner
        runner.add_done_callback(lambda _t, task_id=task.id: _running.pop(task_id, None))
    logger.info("Hintergrund-Vorgang gestartet: %s (kind=%s, key=%s)", task.id, kind, key)
    return task


async def _finish(
    session_factory: SessionFactory,
    task_id: str,
    status: TaskStatus,
    *,
    result: Any = None,
    error: str | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "finished_at": _now()}
    if status == TaskStatus.SUCCEEDED:
        values["result"] = result
        values["progress_percent"] = 100
    if error is not None:
        values["error"] = error[:_MAX_ERROR_LEN]
    async with session_factory() as session:
        await session.execute(
            update(BackgroundTask).where(BackgroundTask.id == task_id).values(**values)
        )
        await session.commit()


def _error_message(exc: BaseException) -> str:
    if isinstance(exc, TaskError):
        return exc.message
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail if isinstance(detail, str) else str(detail)
    # Der Task-Fehler geht per GET /api/tasks an den Client -> nur der Typ;
    # die Details stehen im Log (logger.exception in _run).
    return internal_error_message(exc)


async def _run(ctx: TaskContext, work: TaskWork) -> None:
    factory = ctx.session_factory
    try:
        async with factory() as session:
            await session.execute(
                update(BackgroundTask)
                .where(BackgroundTask.id == ctx.task_id)
                .values(status=TaskStatus.RUNNING, started_at=_now())
            )
            await session.commit()

        raw_result = await work(ctx)
        result = jsonable_encoder(raw_result) if raw_result is not None else None
    except asyncio.CancelledError:
        # Shutdown: best effort als FAILED markieren (Shield, damit das Update
        # nicht selbst gecancelt wird). Bleibt es haengen, raeumt
        # recover_interrupted_tasks beim naechsten Start auf.
        try:
            await asyncio.shield(_finish(factory, ctx.task_id, TaskStatus.FAILED, error=SHUTDOWN_MESSAGE))
        except Exception:
            logger.warning("Task %s konnte beim Abbruch nicht markiert werden", ctx.task_id, exc_info=True)
        raise
    except Exception as exc:
        if isinstance(exc, (TaskError, HTTPException)):
            logger.warning("Hintergrund-Vorgang %s (%s) fehlgeschlagen: %s", ctx.task_id, ctx.kind, _error_message(exc))
        else:
            logger.exception("Hintergrund-Vorgang %s (%s) mit unerwartetem Fehler abgebrochen", ctx.task_id, ctx.kind)
        try:
            await _finish(factory, ctx.task_id, TaskStatus.FAILED, error=_error_message(exc))
        except Exception:
            logger.exception("Task %s konnte nicht als FAILED markiert werden", ctx.task_id)
        return

    try:
        await _finish(factory, ctx.task_id, TaskStatus.SUCCEEDED, result=result)
    except Exception as exc:
        # z.B. Ergebnis nicht JSON-serialisierbar fuer die Spalte.
        logger.exception("Ergebnis von Task %s konnte nicht gespeichert werden", ctx.task_id)
        try:
            await _finish(factory, ctx.task_id, TaskStatus.FAILED, error=_error_message(exc))
        except Exception:
            logger.exception("Task %s konnte nicht als FAILED markiert werden", ctx.task_id)
        return
    logger.info("Hintergrund-Vorgang %s (%s) abgeschlossen", ctx.task_id, ctx.kind)


async def wait_for_task(task_id: str, timeout: float | None = None) -> None:
    """Wartet bis der in-process laufende Vorgang fertig ist (fuer Tests/Shutdown).

    No-op wenn der Vorgang nicht (mehr) in diesem Prozess laeuft.
    """
    runner = _running.get(task_id)
    if runner is None:
        return
    await asyncio.wait_for(asyncio.shield(runner), timeout=timeout)


async def recover_interrupted_tasks(session_factory: SessionFactory) -> int:
    """Beim App-Start: alle PENDING/RUNNING-Vorgaenge auf FAILED setzen.

    Vorgaenge laufen ausschliesslich in-process — beim Start kann keiner mehr
    einen Besitzer haben, daher ohne Alters-Schwelle.
    """
    async with session_factory() as session:
        result = await session.execute(
            update(BackgroundTask)
            .where(BackgroundTask.status.in_(ACTIVE_TASK_STATUSES))
            .values(status=TaskStatus.FAILED, error=INTERRUPTED_MESSAGE, finished_at=_now())
        )
        await session.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("%d unterbrochene(r) Hintergrund-Vorgang/Vorgaenge auf FAILED gesetzt", count)
    return count


async def prune_finished_tasks(
    session_factory: SessionFactory, older_than_days: int = FINISHED_RETENTION_DAYS
) -> int:
    """Abgeschlossene Vorgaenge aelter als ``older_than_days`` loeschen."""
    threshold = _now() - timedelta(days=older_than_days)
    async with session_factory() as session:
        result = await session.execute(
            delete(BackgroundTask)
            .where(BackgroundTask.status.in_((TaskStatus.SUCCEEDED, TaskStatus.FAILED)))
            .where(BackgroundTask.finished_at < threshold)
        )
        await session.commit()
    return result.rowcount or 0


async def shutdown_tasks() -> None:
    """Beim App-Shutdown laufende Vorgaenge abbrechen (markiert sie als FAILED)."""
    runners = list(_running.values())
    for runner in runners:
        runner.cancel()
    if runners:
        await asyncio.gather(*runners, return_exceptions=True)
