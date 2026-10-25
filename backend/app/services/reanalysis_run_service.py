"""Massenlauf der Neu-Analyse (Issue #39).

Ein Lauf ist eine persistente Warteschlange (``reanalysis_runs`` +
``reanalysis_run_items``): er ueberlebt Neustarts (:func:`recover_reanalysis_runs`
setzt unterbrochene Eintraege zurueck) und macht danach weiter. Hoechstens ein
aktiver Lauf (Partial Unique Index, sonst :class:`RunConflictError`).

Der Runner (:func:`run_reanalysis_runner`) arbeitet ein Dokument nach dem
anderen ab und legt pro Dokument einen Aenderungsvorschlag an
(``create_proposal``). Vorrang haben immer neue Eingaenge: solange ein
``ProcessingJob`` wartet oder laeuft, die Queue pausiert ist oder der
Analyse-Slot belegt ist, nimmt er kein Dokument. Er haelt nie eine
Schreib-Transaktion ueber OCR/LLM: Eintrag in kurzer Transaktion beanspruchen,
committen, analysieren (eigene Sessions), Ergebnis in einer weiteren kurzen
Transaktion vermerken.

Ist die KI nicht erreichbar (``WAIT_KINDS``, z.B. direkt nach einem
Modellwechsel, solange der Auto-Pull laeuft), geht der Eintrag zurueck in die
Warteschlange, der Lauf steht auf ``WAITING_AI`` und der Runner wartet mit
Backoff (``WAIT_BACKOFF_S``) — statt alle restlichen Dokumente als Fehler zu
verbuchen. Nach dem naechsten Erfolg steht der Lauf wieder auf ``RUNNING`` und
der Backoff beginnt von vorn. Zeitueberschreitung und Serverfehler koennen aber
auch am Dokument liegen: nach ``MAX_WAIT_ATTEMPTS`` Versuchen am selben Eintrag
(``CAPPED_WAIT_KINDS``) wird er ``FAILED`` und der Lauf macht mit dem naechsten
weiter — sonst stuende er wegen eines Dokuments die ganze Nacht.

Vor jeder Analyse wird geprueft, ob das Dokument noch da und nicht inzwischen
freigegeben ist (``REVIEWED`` wird sonst nur beim Start ausgeschlossen).

Pausieren waehrend einer Analyse: das laufende Dokument wird fertig, danach
steht der Lauf. Abbrechen waehrend einer Analyse: das Ergebnis des laufenden
Dokuments wird noch vermerkt (ein Vorschlag bleibt erhalten), die offenen
Eintraege sind bereits ``SKIPPED``.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import exists, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.public_errors import internal_error_message
from app.models.document import Document, ReviewStatus
from app.models.processing_job import JobStatus, ProcessingJob
from app.models.reanalysis import (
    ACTIVE_RUN_STATUSES,
    ReanalysisProposal,
    ReanalysisRun,
    ReanalysisRunItem,
    RunItemStatus,
    RunStatus,
)
from app.schemas.reanalysis import RunFailure, RunResponse
from app.services.analysis_service import analysis_slot
from app.services.document_lookup import not_deleted
from app.services.notification_service import notify_reanalysis_done
from app.services.ollama_client import OllamaErrorKind
from app.services.queue_worker_service import is_queue_paused
from app.services.reanalysis_service import (
    DOCUMENT_GONE_MESSAGE,
    ReanalysisLLMError,
    create_proposal,
)
from app.services.task_service import TaskError

logger = logging.getLogger(__name__)

# Grobe Dauer-Schaetzung pro Dokument (CPU-Standardmodell); mit OCR doppelt.
SECONDS_PER_DOCUMENT = 45
OCR_FACTOR = 2
# Wartezeiten, solange die KI nicht erreichbar ist; danach immer der letzte Wert.
WAIT_BACKOFF_S = (30, 60, 120, 300, 900)
IDLE_POLL_S = 5
WAIT_KINDS = frozenset({
    OllamaErrorKind.UNREACHABLE,
    OllamaErrorKind.TIMEOUT,
    OllamaErrorKind.MODEL_MISSING,
    OllamaErrorKind.SERVER_ERROR,
})
# Diese KI-Fehler koennen an einem einzelnen Dokument haengen: hoechstens
# MAX_WAIT_ATTEMPTS Versuche je Eintrag, danach FAILED. UNREACHABLE und
# MODEL_MISSING liegen nie am Dokument -> warten ohne Obergrenze.
CAPPED_WAIT_KINDS = frozenset({OllamaErrorKind.TIMEOUT, OllamaErrorKind.SERVER_ERROR})
MAX_WAIT_ATTEMPTS = 3

EMPTY_SELECTION_MESSAGE = "Keine Dokumente zum Neu-Analysieren."
RUN_CONFLICT_MESSAGE = "Es läuft bereits eine Neu-Analyse."
RUN_NOT_FOUND_MESSAGE = "Diesen Neu-Analyse-Lauf gibt es nicht."
RUN_FINISHED_MESSAGE = "Der Neu-Analyse-Lauf ist bereits beendet."
RUN_FAILURES_LIMIT = 50
REVIEWED_MEANWHILE_MESSAGE = "Das Dokument wurde inzwischen freigegeben."

_INTAKE_STATUSES = (JobStatus.PENDING, JobStatus.PROCESSING)
_OPEN_ITEM_STATUSES = (RunItemStatus.PENDING, RunItemStatus.PROCESSING)
# Laeufe, deren Eintraege der Runner abarbeitet (PAUSED nicht).
_WORKING_RUN_STATUSES = (RunStatus.RUNNING, RunStatus.WAITING_AI)

# Modulweite Referenz, damit Tests ohne Wartezeit laufen (monkeypatch).
_sleep = asyncio.sleep


class RunConflictError(Exception):
    """Es gibt bereits einen aktiven Lauf (RUNNING/PAUSED/WAITING_AI)."""

    def __init__(self, active_run: ReanalysisRun, message: str = RUN_CONFLICT_MESSAGE):
        super().__init__(message)
        self.active_run = active_run
        self.message = message


class RunStateError(Exception):
    """Die Aktion passt nicht zum Zustand des Laufs (z.B. bereits beendet)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class RunPreview:
    eligible: int
    reviewed_excluded: int
    no_text_excluded: int
    deleted_or_missing: int
    estimate_seconds: int
    model: str


class _Backoff:
    """Zaehler der aufeinanderfolgenden KI-Ausfaelle (prozesslokal)."""

    def __init__(self) -> None:
        self.attempt = 0

    def next_delay(self) -> int:
        delay = WAIT_BACKOFF_S[min(self.attempt, len(WAIT_BACKOFF_S) - 1)]
        self.attempt += 1
        return delay

    def reset(self) -> None:
        self.attempt = 0


_backoff = _Backoff()
# Versuche mit CAPPED_WAIT_KINDS je Eintrag (prozesslokal; ein Neustart beginnt
# wieder bei 0 — dann gibt es eben bis zu MAX_WAIT_ATTEMPTS weitere Versuche).
_wait_attempts: dict[int, int] = {}


def reset_backoff() -> None:
    _backoff.reset()
    _wait_attempts.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Auswahl / Start
# ---------------------------------------------------------------------------


def _has_text():
    """SQL: Dokument hat gespeicherten Text (nicht nur Leerraum)."""
    return func.length(func.trim(func.coalesce(Document.ocr_text, ""), " \t\r\n")) > 0


async def preview_run(
    session: AsyncSession,
    settings: Settings,
    *,
    document_ids: list[str] | None,
    outdated: bool,
    with_ocr: bool,
) -> tuple[RunPreview, list[str]]:
    """Welche Dokumente ein Lauf erfassen wuerde (nach ``created_at``) und warum andere nicht.

    Nie erfasst: geloeschte und freigegebene (``REVIEWED``) Dokumente, ohne
    ``with_ocr`` auch keine ohne gespeicherten Text. ``outdated``: nur
    Dokumente, die nicht mit ``OLLAMA_MODEL`` analysiert wurden und keinen
    offenen Vorschlag dieses Modells haben.
    """
    if outdated and document_ids is not None:
        raise ValueError("document_ids und outdated schließen sich aus.")

    query = select(Document.id, Document.review_status, _has_text().label("has_text")).where(not_deleted())
    if outdated:
        model = settings.OLLAMA_MODEL
        query = query.where(
            or_(Document.analysis_model.is_(None), Document.analysis_model != model)
        ).where(
            ~exists().where(ReanalysisProposal.document_id == Document.id)
            .where(ReanalysisProposal.model == model)
        )
    else:
        requested = list(dict.fromkeys(document_ids or []))
        query = query.where(Document.id.in_(requested))
    # created_at hat Sekundenaufloesung (server_default) -> id als stabile Zweitsortierung.
    rows = (await session.execute(query.order_by(Document.created_at.asc(), Document.id.asc()))).all()

    reviewed = no_text = 0
    selected: list[str] = []
    for doc_id, review_status, has_text in rows:
        if review_status == ReviewStatus.REVIEWED:
            reviewed += 1
        elif not with_ocr and not has_text:
            no_text += 1
        else:
            selected.append(doc_id)

    missing = 0 if outdated else len(requested) - len(rows)
    factor = OCR_FACTOR if with_ocr else 1
    preview = RunPreview(
        eligible=len(selected),
        reviewed_excluded=reviewed,
        no_text_excluded=no_text,
        deleted_or_missing=missing,
        estimate_seconds=len(selected) * SECONDS_PER_DOCUMENT * factor,
        model=settings.OLLAMA_MODEL,
    )
    return preview, selected


async def _active_run(session: AsyncSession) -> ReanalysisRun | None:
    result = await session.execute(
        select(ReanalysisRun).where(ReanalysisRun.status.in_(ACTIVE_RUN_STATUSES)).limit(1)
    )
    return result.scalar_one_or_none()


async def start_run(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    document_ids: list[str] | None,
    outdated: bool,
    with_ocr: bool,
) -> ReanalysisRun:
    """Legt Lauf + Eintraege in einer Transaktion an.

    Raises:
        ValueError: keine passenden Dokumente (``EMPTY_SELECTION_MESSAGE``).
        RunConflictError: es gibt bereits einen aktiven Lauf.
    """
    async with session_factory() as session:
        active = await _active_run(session)
        if active is not None:
            raise RunConflictError(active)
        _, selected = await preview_run(
            session, settings, document_ids=document_ids, outdated=outdated, with_ocr=with_ocr,
        )
        if not selected:
            raise ValueError(EMPTY_SELECTION_MESSAGE)

        run = ReanalysisRun(
            status=RunStatus.RUNNING, model=settings.OLLAMA_MODEL,
            with_ocr=with_ocr, total=len(selected),
        )
        session.add(run)
        try:
            await session.flush()
            await session.execute(insert(ReanalysisRunItem), [
                {"run_id": run.id, "document_id": doc_id, "position": position,
                 "status": RunItemStatus.PENDING}
                for position, doc_id in enumerate(selected)
            ])
            await session.commit()
        except IntegrityError:
            # Partial Unique Index: parallel gestarteter Lauf.
            await session.rollback()
            active = await _active_run(session)
            if active is None:
                raise
            raise RunConflictError(active) from None
        await session.refresh(run)
    reset_backoff()  # Ausfaelle eines frueheren Laufs zaehlen nicht mit
    logger.info("Neu-Analyse-Lauf %s gestartet: %d Dokumente (OCR=%s)", run.id, run.total, with_ocr)
    return run


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def recover_reanalysis_runs(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Beim Start: durch den Neustart unterbrochene Eintraege zurueck in die Warteschlange."""
    async with session_factory() as session:
        result = await session.execute(
            update(ReanalysisRunItem)
            .where(ReanalysisRunItem.status == RunItemStatus.PROCESSING)
            .values(status=RunItemStatus.PENDING)
        )
        await session.commit()
    if result.rowcount:
        logger.info("%d unterbrochene Neu-Analyse(n) wieder eingereiht", result.rowcount)
    return result.rowcount


async def _intake_busy(session: AsyncSession) -> bool:
    """Neue Eingaenge haben Vorrang: wartende/laufende Jobs oder belegter Analyse-Slot."""
    if analysis_slot().locked():
        return True
    return bool(await session.scalar(
        select(exists().where(ProcessingJob.status.in_(_INTAKE_STATUSES)))
    ))


async def _skip_reason(session_factory, document_id: str) -> str | None:
    """Grund, das Dokument nicht (mehr) zu analysieren: geloescht oder inzwischen freigegeben."""
    async with session_factory() as session:
        row = (await session.execute(
            select(Document.review_status).where(Document.id == document_id).where(not_deleted())
        )).first()
    if row is None:
        return DOCUMENT_GONE_MESSAGE
    if row.review_status == ReviewStatus.REVIEWED:
        return REVIEWED_MEANWHILE_MESSAGE
    return None


@dataclass
class _ItemResult:
    status: RunItemStatus | None  # None = KI nicht erreichbar, erneut versuchen
    error: str | None = None
    wait_kind: OllamaErrorKind | None = None  # bei status None: Art des KI-Ausfalls
    # Eintrag nach MAX_WAIT_ATTEMPTS aufgegeben: der Lauf wartet nicht mehr.
    gave_up_waiting: bool = False


async def _analyse_item(
    session_factory, settings: Settings, run_id: str, item_id: int, document_id: str, *, with_ocr: bool,
) -> _ItemResult:
    """Analysiert einen Eintrag; begrenzt die Versuche bei ``CAPPED_WAIT_KINDS``."""
    result = await _analyse_document(session_factory, settings, run_id, document_id, with_ocr=with_ocr)
    if result.status is None and result.wait_kind in CAPPED_WAIT_KINDS:
        attempts = _wait_attempts.get(item_id, 0) + 1
        if attempts < MAX_WAIT_ATTEMPTS:
            _wait_attempts[item_id] = attempts
            return result
        result = _ItemResult(RunItemStatus.FAILED, result.error, gave_up_waiting=True)
    if result.status is not None:
        _wait_attempts.pop(item_id, None)
    return result


async def _analyse_document(
    session_factory, settings: Settings, run_id: str, document_id: str, *, with_ocr: bool,
) -> _ItemResult:
    skip = await _skip_reason(session_factory, document_id)
    if skip is not None:
        return _ItemResult(RunItemStatus.SKIPPED, skip)
    try:
        outcome = await create_proposal(
            None, session_factory, document_id, settings, with_ocr=with_ocr, run_id=run_id,
        )
    except ReanalysisLLMError as exc:
        if exc.kind in WAIT_KINDS:
            return _ItemResult(None, exc.message, wait_kind=exc.kind)
        return _ItemResult(RunItemStatus.FAILED, exc.message)
    except TaskError as exc:
        # Waehrend der Analyse geloescht -> uebersprungen, sonst echter Fehler.
        if await _skip_reason(session_factory, document_id) == DOCUMENT_GONE_MESSAGE:
            return _ItemResult(RunItemStatus.SKIPPED, DOCUMENT_GONE_MESSAGE)
        return _ItemResult(RunItemStatus.FAILED, exc.message)
    except Exception as exc:
        logger.exception("Neu-Analyse von Dokument %s im Lauf %s fehlgeschlagen", document_id, run_id)
        return _ItemResult(RunItemStatus.FAILED, internal_error_message(exc))
    status = RunItemStatus.PROPOSED if outcome.status == "proposed" else RunItemStatus.UNCHANGED
    return _ItemResult(status)


async def _count_items(session: AsyncSession, run_id: str) -> dict[str, int]:
    result = await session.execute(
        select(ReanalysisRunItem.status, func.count())
        .where(ReanalysisRunItem.run_id == run_id)
        .group_by(ReanalysisRunItem.status)
    )
    return {RunItemStatus(status).value: count for status, count in result.all()}


async def _finish_if_complete(session: AsyncSession, run_id: str) -> bool:
    """Lauf auf DONE + Benachrichtigung, wenn kein Eintrag mehr offen ist.

    Nur RUNNING/WAITING_AI-Laeufe werden abgeschlossen: ein Lauf, der waehrend
    seines letzten Dokuments pausiert wurde, bleibt PAUSED und wird erst nach
    dem Fortsetzen (naechster ``process_next_item``-Aufruf) DONE.
    Der bedingte UPDATE sorgt dafuer, dass nur genau ein Aufruf benachrichtigt.
    Der Aufrufer committet.
    """
    open_items = await session.scalar(
        select(exists().where(ReanalysisRunItem.run_id == run_id)
               .where(ReanalysisRunItem.status.in_(_OPEN_ITEM_STATUSES)))
    )
    if open_items:
        return False
    result = await session.execute(
        update(ReanalysisRun)
        .where(ReanalysisRun.id == run_id)
        .where(ReanalysisRun.status.in_(_WORKING_RUN_STATUSES))
        .values(status=RunStatus.DONE, finished_at=_now(), last_error=None)
    )
    if result.rowcount != 1:
        return False
    counts = await _count_items(session, run_id)
    notify_reanalysis_done(
        session,
        proposed=counts.get(RunItemStatus.PROPOSED.value, 0),
        unchanged=counts.get(RunItemStatus.UNCHANGED.value, 0),
        failed=counts.get(RunItemStatus.FAILED.value, 0),
    )
    logger.info("Neu-Analyse-Lauf %s fertig: %s", run_id, counts)
    return True


async def _record_result(session_factory, run_id: str, item_id: int, result: _ItemResult) -> int | None:
    """Vermerkt das Ergebnis; gibt bei KI-Ausfall die Backoff-Wartezeit zurueck.

    Alle Schreibzugriffe sind bedingte UPDATEs: ein gleichzeitig committetes
    Abbrechen kann den Eintrag nicht wieder in die Warteschlange eines
    beendeten Laufs stellen (er wird dann SKIPPED).
    """
    async with session_factory() as session:
        if result.status is None:
            delay = await _record_wait(session, run_id, item_id, result.error)
        else:
            delay = None
            await _record_outcome(session, run_id, item_id, result)
        await _finish_if_complete(session, run_id)
        await session.commit()
    return delay


async def _record_wait(session: AsyncSession, run_id: str, item_id: int, error: str | None) -> int | None:
    """KI nicht erreichbar: Eintrag zurueck in die Warteschlange, Lauf WAITING_AI."""
    if not await _requeue_or_skip(session, run_id, item_id):
        return None  # Lauf inzwischen beendet -> Eintrag SKIPPED, kein Warten
    await session.execute(
        update(ReanalysisRun).where(ReanalysisRun.id == run_id)
        .where(ReanalysisRun.status.in_(ACTIVE_RUN_STATUSES))
        .values(last_error=error)
    )
    await session.execute(
        update(ReanalysisRun).where(ReanalysisRun.id == run_id)
        .where(ReanalysisRun.status == RunStatus.RUNNING)
        .values(status=RunStatus.WAITING_AI)
    )
    # Nach den UPDATEs haelt die Session die Schreibsperre: der Status ist stabil.
    run_status = await session.scalar(select(ReanalysisRun.status).where(ReanalysisRun.id == run_id))
    # Pausiert: kein Warten, das Fortsetzen soll sofort wirken.
    if run_status not in _WORKING_RUN_STATUSES:
        return None
    return _backoff.next_delay()


async def _record_outcome(session: AsyncSession, run_id: str, item_id: int, result: _ItemResult) -> None:
    """Ergebnis vermerken — auch in einem inzwischen abgebrochenen Lauf (Vorschlag bleibt)."""
    # Nur ein Erfolg zeigt, dass die KI wieder antwortet (uebersprungene
    # oder fehlgeschlagene Dokumente erreichen sie evtl. gar nicht). Ein nach
    # MAX_WAIT_ATTEMPTS aufgegebener Eintrag beendet das Warten ebenfalls: der
    # naechste Eintrag beginnt mit frischem Backoff.
    if result.status in (RunItemStatus.PROPOSED, RunItemStatus.UNCHANGED) or result.gave_up_waiting:
        _backoff.reset()
        await session.execute(
            update(ReanalysisRun).where(ReanalysisRun.id == run_id)
            .where(ReanalysisRun.status == RunStatus.WAITING_AI)
            .values(status=RunStatus.RUNNING, last_error=None)
        )
    await session.execute(
        update(ReanalysisRunItem)
        .where(ReanalysisRunItem.id == item_id)
        .where(ReanalysisRunItem.status == RunItemStatus.PROCESSING)
        .values(status=result.status, error=result.error)
    )


async def _requeue_or_skip(session: AsyncSession, run_id: str, item_id: int) -> bool:
    """PROCESSING-Eintrag zurueck auf PENDING, solange der Lauf aktiv ist, sonst SKIPPED.

    Der Laufstatus wird im selben Statement geprueft (kein Lesen-dann-Schreiben).
    ``True``, wenn der Eintrag wieder eingereiht wurde. Der Aufrufer committet.
    """
    run_active = (
        exists().where(ReanalysisRun.id == run_id)
        .where(ReanalysisRun.status.in_(ACTIVE_RUN_STATUSES))
    )
    requeued = await session.execute(
        update(ReanalysisRunItem)
        .where(ReanalysisRunItem.id == item_id)
        .where(ReanalysisRunItem.status == RunItemStatus.PROCESSING)
        .where(run_active)
        .values(status=RunItemStatus.PENDING)
    )
    await session.execute(
        update(ReanalysisRunItem)
        .where(ReanalysisRunItem.id == item_id)
        .where(ReanalysisRunItem.status == RunItemStatus.PROCESSING)
        .values(status=RunItemStatus.SKIPPED)
    )
    return requeued.rowcount == 1


async def _release_orphaned_items(session: AsyncSession) -> None:
    """Selbstheilung: zwischen zwei Aufrufen gibt es keinen legitimen PROCESSING-Eintrag.

    Es gibt genau einen In-Process-Runner, und ``process_next_item`` laeuft
    sequenziell. Ein PROCESSING-Eintrag stammt also aus einem Fehler, bei dem
    selbst das Freigeben scheiterte: zurueck auf PENDING (aktiver Lauf) bzw.
    SKIPPED (beendeter Lauf). Committet selbst (nur wenn es etwas gab).
    """
    orphaned = (await session.execute(
        select(ReanalysisRunItem.id, ReanalysisRunItem.run_id)
        .where(ReanalysisRunItem.status == RunItemStatus.PROCESSING)
    )).all()
    if not orphaned:
        return
    for item_id, run_id in orphaned:
        await _requeue_or_skip(session, run_id, item_id)
    await session.commit()
    logger.warning("%d haengende Neu-Analyse-Eintraege freigegeben", len(orphaned))


async def wait_for_ai(session_factory, run_id: str, delay: float) -> None:
    """Backoff-Wartezeit in kurzen Abschnitten (``IDLE_POLL_S``).

    Endet vorzeitig, sobald der Lauf nicht mehr auf die KI wartet (pausiert,
    fortgesetzt, abgebrochen) — sonst wirkte eine Aktion erst nach bis zu 15 min.
    """
    remaining = delay
    while remaining > 0:
        step = min(IDLE_POLL_S, remaining)
        await _sleep(step)
        remaining -= step
        async with session_factory() as session:
            status = await session.scalar(
                select(ReanalysisRun.status).where(ReanalysisRun.id == run_id)
            )
        if status != RunStatus.WAITING_AI:
            return


async def process_next_item(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> bool:
    """Bearbeitet hoechstens einen Eintrag des aktiven Laufs.

    ``True``: ein Eintrag wurde bearbeitet (bzw. wegen KI-Ausfall wieder
    eingereiht, inkl. Backoff-Wartezeit) oder der Lauf wurde abgeschlossen.
    ``False``: nichts zu tun oder blockiert (kein Lauf, pausiert, Queue
    pausiert, neue Eingaenge haben Vorrang).
    """
    if is_queue_paused():
        return False
    async with session_factory() as session:
        await _release_orphaned_items(session)
        run = await _active_run(session)
        if run is None or run.status == RunStatus.PAUSED:
            return False
        run_id, with_ocr = run.id, run.with_ocr
        if await _intake_busy(session):
            return False
        row = (await session.execute(
            select(ReanalysisRunItem.id, ReanalysisRunItem.document_id)
            .where(ReanalysisRunItem.run_id == run_id)
            .where(ReanalysisRunItem.status == RunItemStatus.PENDING)
            .order_by(ReanalysisRunItem.position)
            .limit(1)
        )).first()
        if row is None:
            finished = await _finish_if_complete(session, run_id)
            await session.commit()
            return finished
        item_id, document_id = row
        claim = await session.execute(
            update(ReanalysisRunItem)
            .where(ReanalysisRunItem.id == item_id)
            .where(ReanalysisRunItem.status == RunItemStatus.PENDING)
            .values(status=RunItemStatus.PROCESSING)
        )
        await session.commit()
        if claim.rowcount == 0:
            return True  # inzwischen abgebrochen oder anderweitig erledigt

    # Ab hier keine offene Transaktion: create_proposal oeffnet eigene Sessions.
    try:
        result = await _analyse_item(
            session_factory, settings, run_id, item_id, document_id, with_ocr=with_ocr,
        )
        delay = await _record_result(session_factory, run_id, item_id, result)
    except Exception:
        # z.B. "database is locked": der Eintrag darf nicht in PROCESSING haengen
        # bleiben. Scheitert auch das Freigeben, raeumt der naechste Aufruf auf
        # (_release_orphaned_items).
        logger.exception("Neu-Analyse-Lauf %s: Dokument %s wird erneut eingereiht", run_id, document_id)
        try:
            async with session_factory() as session:
                await _requeue_or_skip(session, run_id, item_id)
                await session.commit()
        except Exception:
            logger.exception("Neu-Analyse-Lauf %s: Freigeben von Eintrag %s fehlgeschlagen", run_id, item_id)
        return False
    if delay is not None:
        logger.warning(
            "Neu-Analyse-Lauf %s wartet %d s auf die KI: %s", run_id, delay, result.error,
        )
        await wait_for_ai(session_factory, run_id, delay)
    return True


async def run_reanalysis_runner(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Endlos-Loop des Massenlaufs (ein In-Process-Runner, wie der Queue-Worker)."""
    logger.info("Neu-Analyse-Runner gestartet")
    while True:
        try:
            if not await process_next_item(session_factory, settings):
                await _sleep(IDLE_POLL_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Fehler im Neu-Analyse-Runner")
            await _sleep(IDLE_POLL_S)


# ---------------------------------------------------------------------------
# Steuerung / Status
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[str, tuple[tuple[RunStatus, ...], RunStatus]] = {
    "pause": (_WORKING_RUN_STATUSES, RunStatus.PAUSED),
    "resume": ((RunStatus.PAUSED,), RunStatus.RUNNING),
    "cancel": (ACTIVE_RUN_STATUSES, RunStatus.CANCELLED),
}


async def set_run_status(
    session: AsyncSession,
    run_id: str,
    action: Literal["pause", "resume", "cancel"],
) -> ReanalysisRun:
    """Pausieren, Fortsetzen oder Abbrechen. Der Aufrufer committet.

    Bereits erreichte Zielzustaende sind kein Fehler (Fortsetzen eines
    laufenden Laufs). Abbrechen setzt offene Eintraege auf ``SKIPPED``; schon
    erzeugte Vorschlaege bleiben.

    Raises:
        LookupError: unbekannter Lauf.
        RunStateError: Lauf ist bereits beendet.
    """
    sources, target = _TRANSITIONS[action]
    run = await session.get(ReanalysisRun, run_id, populate_existing=True)
    if run is None:
        raise LookupError(RUN_NOT_FOUND_MESSAGE)
    if action == "resume" and run.status in _WORKING_RUN_STATUSES:
        return run
    if run.status == target:
        return run
    values: dict = {"status": target}
    if target == RunStatus.CANCELLED:
        values["finished_at"] = _now()
    # Bedingt: der Runner kann den Lauf gleichzeitig abschliessen.
    result = await session.execute(
        update(ReanalysisRun).where(ReanalysisRun.id == run_id)
        .where(ReanalysisRun.status.in_(sources)).values(**values)
    )
    if result.rowcount != 1:
        raise RunStateError(RUN_FINISHED_MESSAGE)
    if target == RunStatus.CANCELLED:
        await session.execute(
            update(ReanalysisRunItem)
            .where(ReanalysisRunItem.run_id == run_id)
            .where(ReanalysisRunItem.status == RunItemStatus.PENDING)
            .values(status=RunItemStatus.SKIPPED)
        )
    await session.refresh(run)
    return run


async def current_run(session: AsyncSession) -> ReanalysisRun | None:
    """Der aktive Lauf, sonst der zuletzt angelegte (beendete)."""
    active = await _active_run(session)
    if active is not None:
        return active
    result = await session.execute(
        select(ReanalysisRun).order_by(ReanalysisRun.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def run_summary(session: AsyncSession, run: ReanalysisRun) -> RunResponse:
    """Fortschritt und Ergebnis eines Laufs inkl. der ersten Fehler mit Grund."""
    counts = await _count_items(session, run.id)
    proposed = counts.get(RunItemStatus.PROPOSED.value, 0)
    unchanged = counts.get(RunItemStatus.UNCHANGED.value, 0)
    failed = counts.get(RunItemStatus.FAILED.value, 0)
    skipped = counts.get(RunItemStatus.SKIPPED.value, 0)

    rows = (await session.execute(
        select(ReanalysisRunItem.document_id, Document.title, ReanalysisRunItem.error)
        .outerjoin(Document, Document.id == ReanalysisRunItem.document_id)
        .where(ReanalysisRunItem.run_id == run.id)
        .where(ReanalysisRunItem.status.in_((RunItemStatus.FAILED, RunItemStatus.SKIPPED)))
        .where(ReanalysisRunItem.error.is_not(None))
        .order_by(ReanalysisRunItem.position)
        .limit(RUN_FAILURES_LIMIT)
    )).all()

    return RunResponse(
        id=run.id,
        status=RunStatus(run.status).value,
        model=run.model,
        with_ocr=run.with_ocr,
        total=run.total,
        done=proposed + unchanged + failed + skipped,
        proposed=proposed,
        unchanged=unchanged,
        failed=failed,
        skipped=skipped,
        last_error=run.last_error,
        created_at=run.created_at,
        finished_at=run.finished_at,
        failures=[RunFailure(document_id=d, title=t, error=e) for d, t, e in rows],
    )
