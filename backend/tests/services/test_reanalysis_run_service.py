"""Massenlauf der Neu-Analyse (Issue #39): Auswahl, Vorrang neuer Eingaenge,
Warten auf die KI, Neustart, Pausieren/Abbrechen."""

import itertools
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models.document import DocumentStatus, ReviewStatus
from app.models.notification import Notification, NotificationType
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.models.reanalysis import (
    ReanalysisProposal,
    ReanalysisRun,
    ReanalysisRunItem,
    RunItemStatus,
    RunStatus,
)
from app.services import queue_worker_service, reanalysis_run_service
from app.services.ollama_client import OllamaErrorKind
from app.services.reanalysis_run_service import (
    OCR_FACTOR,
    SECONDS_PER_DOCUMENT,
    RunConflictError,
    preview_run,
    process_next_item,
    recover_reanalysis_runs,
    run_summary,
    set_run_status,
    start_run,
)
from app.services.reanalysis_service import (
    DOCUMENT_GONE_MESSAGE,
    ProposalOutcome,
    ReanalysisLLMError,
)
from app.services.task_service import TaskError
from tests.factories import make_document

MODEL = "gemma-aktuell"


@pytest.fixture
def settings(test_settings):
    return test_settings.model_copy(update={"OLLAMA_MODEL": MODEL})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Backoff- und Leerlauf-Schlaf ohne Wartezeit; Aufrufe werden mitgeschrieben."""
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(reanalysis_run_service, "_sleep", fake_sleep)
    reanalysis_run_service.reset_backoff()
    yield sleeps
    reanalysis_run_service.reset_backoff()


@pytest.fixture
def waits(monkeypatch):
    """Backoff-Wartezeiten (Sekunden) je KI-Ausfall, ohne zu warten."""
    delays: list[float] = []

    async def fake_wait(session_factory, run_id, delay):
        delays.append(delay)

    monkeypatch.setattr(reanalysis_run_service, "wait_for_ai", fake_wait)
    return delays


@pytest.fixture
def fake_proposal(monkeypatch):
    """Ersetzt create_proposal; ``results`` je Dokument: "proposed" | "unchanged" | Exception."""
    state: dict = {"results": {}, "calls": []}

    async def fake_create(ctx, session_factory, document_id, settings, *, with_ocr, run_id=None):
        state["calls"].append({"document_id": document_id, "with_ocr": with_ocr, "run_id": run_id})
        result = state["results"].get(document_id, "proposed")
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return await result()
        return ProposalOutcome(result, [])

    monkeypatch.setattr(reanalysis_run_service, "create_proposal", fake_create)
    return state


_created = itertools.count()


async def _doc(db, **overrides):
    """Dokument mit eindeutigem, aufsteigendem ``created_at`` (Reihenfolge der Laeufe)."""
    overrides.setdefault("created_at", datetime(2026, 1, 1) + timedelta(minutes=next(_created)))
    doc = await make_document(db, **overrides)
    await db.commit()
    return doc


async def _start(session_factory, settings, ids, with_ocr=False) -> ReanalysisRun:
    return await start_run(session_factory, settings, document_ids=ids, outdated=False, with_ocr=with_ocr)


async def _items(session_factory, run_id) -> list[ReanalysisRunItem]:
    async with session_factory() as session:
        result = await session.execute(
            select(ReanalysisRunItem).where(ReanalysisRunItem.run_id == run_id)
            .order_by(ReanalysisRunItem.position)
        )
        return list(result.scalars().all())


async def _run(session_factory, run_id) -> ReanalysisRun:
    async with session_factory() as session:
        return await session.get(ReanalysisRun, run_id)


async def run_until_idle(session_factory, settings, max_steps=20) -> int:
    """Ruft ``process_next_item`` wie der Runner auf, bis nichts mehr zu tun ist."""
    steps = 0
    while steps < max_steps and await process_next_item(session_factory, settings):
        steps += 1
    return steps


async def _add_job(db, status=JobStatus.PENDING) -> ProcessingJob:
    job = ProcessingJob(
        original_filename="neu.pdf", stored_filename="neu.pdf", file_path="/x/neu.pdf",
        file_type="pdf", file_size_bytes=1, source=JobSource.UPLOAD, status=status,
    )
    db.add(job)
    await db.commit()
    return job


# --- Vorschau / Auswahl ---------------------------------------------------------


async def test_preview_outdated_excludes_reviewed_current_and_open_current_proposal(db_session, settings):
    old = await _doc(db_session, analysis_model="altes-modell")
    never = await _doc(db_session, analysis_model=None)
    await _doc(db_session, analysis_model=MODEL)  # aktuell
    await _doc(db_session, analysis_model="altes-modell", review_status=ReviewStatus.REVIEWED)
    await _doc(db_session, analysis_model="altes-modell", status=DocumentStatus.DELETED)
    with_current = await _doc(db_session, analysis_model="altes-modell")
    with_old = await _doc(db_session, analysis_model="altes-modell")
    db_session.add_all([
        ReanalysisProposal(document_id=with_current.id, model=MODEL, analysis={}),
        ReanalysisProposal(document_id=with_old.id, model="altes-modell", analysis={}),
    ])
    await db_session.commit()

    preview, ids = await preview_run(db_session, settings, document_ids=None, outdated=True, with_ocr=False)

    assert ids == [old.id, never.id, with_old.id]  # nach created_at
    assert preview.eligible == 3
    assert preview.reviewed_excluded == 1
    assert preview.no_text_excluded == 0
    assert preview.deleted_or_missing == 0
    assert preview.estimate_seconds == 3 * SECONDS_PER_DOCUMENT


async def test_preview_without_ocr_excludes_documents_without_text(db_session, settings):
    with_text = await _doc(db_session)
    no_text = await _doc(db_session, ocr_text=None)
    blank = await _doc(db_session, ocr_text=" \n\t ")
    ids = [with_text.id, no_text.id, blank.id]

    preview, selected = await preview_run(db_session, settings, document_ids=ids, outdated=False, with_ocr=False)
    assert selected == [with_text.id]
    assert preview.no_text_excluded == 2

    preview, selected = await preview_run(db_session, settings, document_ids=ids, outdated=False, with_ocr=True)
    assert selected == ids
    assert preview.no_text_excluded == 0


async def test_preview_explicit_ids_excludes_reviewed(db_session, settings):
    ok = await _doc(db_session)
    reviewed = await _doc(db_session, review_status=ReviewStatus.REVIEWED)
    deleted = await _doc(db_session, status=DocumentStatus.DELETED)
    current = await _doc(db_session, analysis_model=MODEL)  # ausdruecklich gewaehlt -> zaehlt

    preview, selected = await preview_run(
        db_session, settings,
        document_ids=[ok.id, reviewed.id, deleted.id, "gibt-es-nicht", current.id],
        outdated=False, with_ocr=False,
    )
    assert selected == [ok.id, current.id]
    assert preview.reviewed_excluded == 1
    assert preview.deleted_or_missing == 2


async def test_estimate_seconds(db_session, settings):
    ids = [(await _doc(db_session)).id for _ in range(3)]
    preview, _ = await preview_run(db_session, settings, document_ids=ids, outdated=False, with_ocr=True)
    assert preview.estimate_seconds == 3 * 45 * 2 == 3 * SECONDS_PER_DOCUMENT * OCR_FACTOR


async def test_preview_rejects_ids_with_outdated(db_session, settings):
    with pytest.raises(ValueError):
        await preview_run(db_session, settings, document_ids=["x"], outdated=True, with_ocr=False)


# --- Start ----------------------------------------------------------------------


async def test_start_run_creates_items_in_order(db_session, test_session_factory, settings):
    a = await _doc(db_session)
    b = await _doc(db_session)
    run = await _start(test_session_factory, settings, [b.id, a.id], with_ocr=True)

    assert run.status == RunStatus.RUNNING
    assert run.model == MODEL
    assert run.with_ocr is True
    assert run.total == 2
    items = await _items(test_session_factory, run.id)
    assert [i.document_id for i in items] == [a.id, b.id]
    assert {i.status for i in items} == {RunItemStatus.PENDING}


async def test_start_run_empty_selection_raises(db_session, test_session_factory, settings):
    reviewed = await _doc(db_session, review_status=ReviewStatus.REVIEWED)
    with pytest.raises(ValueError, match="Keine Dokumente zum Neu-Analysieren."):
        await _start(test_session_factory, settings, [reviewed.id])


async def test_second_run_conflict(db_session, test_session_factory, settings):
    doc = await _doc(db_session)
    first = await _start(test_session_factory, settings, [doc.id])
    with pytest.raises(RunConflictError) as info:
        await _start(test_session_factory, settings, [doc.id])
    assert info.value.active_run.id == first.id

    # Auch ein pausierter Lauf ist aktiv.
    async with test_session_factory() as session:
        await set_run_status(session, first.id, "pause")
        await session.commit()
    with pytest.raises(RunConflictError):
        await _start(test_session_factory, settings, [doc.id])

    # Nach dem Abbrechen geht ein neuer Lauf.
    async with test_session_factory() as session:
        await set_run_status(session, first.id, "cancel")
        await session.commit()
    second = await _start(test_session_factory, settings, [doc.id])
    assert second.id != first.id


# --- Runner ---------------------------------------------------------------------


async def test_process_waits_while_jobs_pending(db_session, test_session_factory, settings, fake_proposal):
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])
    job = await _add_job(db_session, JobStatus.PENDING)

    assert await process_next_item(test_session_factory, settings) is False
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PENDING
    assert fake_proposal["calls"] == []

    # Ein Job in Verarbeitung blockiert ebenso.
    job.status = JobStatus.PROCESSING
    await db_session.commit()
    assert await process_next_item(test_session_factory, settings) is False

    # Fertige Jobs blockieren nicht mehr.
    job.status = JobStatus.COMPLETED
    await db_session.commit()
    assert await process_next_item(test_session_factory, settings) is True
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PROPOSED


async def test_process_respects_pause_and_queue_pause(db_session, test_session_factory, settings, fake_proposal):
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])

    async with test_session_factory() as session:
        await set_run_status(session, run.id, "pause")
        await session.commit()
    assert await process_next_item(test_session_factory, settings) is False

    async with test_session_factory() as session:
        await set_run_status(session, run.id, "resume")
        await session.commit()
    queue_worker_service.pause_queue()
    try:
        assert await process_next_item(test_session_factory, settings) is False
    finally:
        queue_worker_service.resume_queue()
    assert fake_proposal["calls"] == []

    assert await process_next_item(test_session_factory, settings) is True
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PROPOSED


async def test_process_without_run_is_idle(test_session_factory, settings, fake_proposal):
    assert await process_next_item(test_session_factory, settings) is False


async def test_run_completes_and_notifies(db_session, test_session_factory, settings, fake_proposal):
    a, b, c = [await _doc(db_session) for _ in range(3)]
    fake_proposal["results"] = {
        a.id: "proposed",
        b.id: "unchanged",
        c.id: TaskError("Kein OCR-Text vorhanden, Re-Analyse nicht möglich."),
    }
    run = await _start(test_session_factory, settings, [a.id, b.id, c.id], with_ocr=True)

    await run_until_idle(test_session_factory, settings)

    assert [call["run_id"] for call in fake_proposal["calls"]] == [run.id] * 3
    assert all(call["with_ocr"] for call in fake_proposal["calls"])
    items = await _items(test_session_factory, run.id)
    assert [i.status for i in items] == [RunItemStatus.PROPOSED, RunItemStatus.UNCHANGED, RunItemStatus.FAILED]
    assert items[2].error == "Kein OCR-Text vorhanden, Re-Analyse nicht möglich."

    finished = await _run(test_session_factory, run.id)
    assert finished.status == RunStatus.DONE
    assert finished.finished_at is not None

    async with test_session_factory() as session:
        summary = await run_summary(session, finished)
        notes = (await session.execute(
            select(Notification).where(Notification.type == NotificationType.REANALYSIS_DONE)
        )).scalars().all()
    assert (summary.proposed, summary.unchanged, summary.failed, summary.skipped) == (1, 1, 1, 0)
    assert summary.done == summary.total == 3
    assert [(f.document_id, f.title) for f in summary.failures] == [(c.id, c.title)]
    assert len(notes) == 1
    assert notes[0].title == "Neu-Analyse fertig"
    assert notes[0].message == "1 Vorschläge, 1 unverändert, 1 Fehler"
    assert notes[0].document_id is None

    # Weitere Durchlaeufe benachrichtigen nicht erneut.
    assert await process_next_item(test_session_factory, settings) is False


async def test_run_waits_when_ollama_unreachable(db_session, test_session_factory, settings, fake_proposal, waits):
    doc = await _doc(db_session)
    fake_proposal["results"] = {doc.id: [
        ReanalysisLLMError("Ollama ist nicht erreichbar.", OllamaErrorKind.UNREACHABLE),
        "proposed",
    ]}
    run = await _start(test_session_factory, settings, [doc.id])

    assert await process_next_item(test_session_factory, settings) is True
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PENDING
    waiting = await _run(test_session_factory, run.id)
    assert waiting.status == RunStatus.WAITING_AI
    assert waiting.last_error == "Ollama ist nicht erreichbar."
    assert waits == [reanalysis_run_service.WAIT_BACKOFF_S[0]]

    assert await process_next_item(test_session_factory, settings) is True
    item = (await _items(test_session_factory, run.id))[0]
    assert item.status == RunItemStatus.PROPOSED
    finished = await _run(test_session_factory, run.id)
    assert finished.status == RunStatus.DONE
    assert finished.last_error is None


async def test_backoff_grows_and_resets(db_session, test_session_factory, settings, fake_proposal, waits):
    a = await _doc(db_session)
    b = await _doc(db_session)
    down = ReanalysisLLMError("weg", OllamaErrorKind.MODEL_MISSING)
    fake_proposal["results"] = {
        a.id: [down] * 7 + ["proposed"],
        b.id: [ReanalysisLLMError("weg", OllamaErrorKind.TIMEOUT), "unchanged"],
    }
    await _start(test_session_factory, settings, [a.id, b.id])

    for _ in range(8):
        await process_next_item(test_session_factory, settings)
    assert waits == [30, 60, 120, 300, 900, 900, 900]

    await run_until_idle(test_session_factory, settings)
    assert waits[-1] == 30  # nach einem Erfolg beginnt der Backoff von vorn


async def test_new_run_starts_backoff_from_scratch(db_session, test_session_factory, settings, fake_proposal, waits):
    a = await _doc(db_session)
    b = await _doc(db_session)
    down = ReanalysisLLMError("weg", OllamaErrorKind.UNREACHABLE)
    fake_proposal["results"] = {a.id: [down, down, down], b.id: [down]}
    first = await _start(test_session_factory, settings, [a.id])
    for _ in range(3):
        await process_next_item(test_session_factory, settings)
    async with test_session_factory() as session:
        await set_run_status(session, first.id, "cancel")
        await session.commit()

    await _start(test_session_factory, settings, [b.id])
    await process_next_item(test_session_factory, settings)
    assert waits == [30, 60, 120, 30]


@pytest.mark.parametrize("kind", [OllamaErrorKind.TIMEOUT, OllamaErrorKind.SERVER_ERROR])
async def test_capped_wait_kind_fails_item_after_max_attempts(
    db_session, test_session_factory, settings, fake_proposal, waits, kind,
):
    """Ein Dokument, das immer in die Zeitueberschreitung laeuft, blockiert den Lauf nicht."""
    stuck = await _doc(db_session)
    ok = await _doc(db_session)
    error = ReanalysisLLMError("Die KI hat nicht rechtzeitig geantwortet.", kind)
    fake_proposal["results"] = {stuck.id: [error] * 10}
    run = await _start(test_session_factory, settings, [stuck.id, ok.id])
    max_attempts = reanalysis_run_service.MAX_WAIT_ATTEMPTS

    for _ in range(max_attempts - 1):
        assert await process_next_item(test_session_factory, settings) is True
        assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PENDING
        assert (await _run(test_session_factory, run.id)).status == RunStatus.WAITING_AI

    assert await process_next_item(test_session_factory, settings) is True  # letzter Versuch
    item = (await _items(test_session_factory, run.id))[0]
    assert (item.status, item.error) == (RunItemStatus.FAILED, "Die KI hat nicht rechtzeitig geantwortet.")
    running = await _run(test_session_factory, run.id)
    assert (running.status, running.last_error) == (RunStatus.RUNNING, None)
    assert waits == list(reanalysis_run_service.WAIT_BACKOFF_S[:max_attempts - 1])

    await run_until_idle(test_session_factory, settings)
    assert [i.status for i in await _items(test_session_factory, run.id)] == [
        RunItemStatus.FAILED, RunItemStatus.PROPOSED,
    ]
    assert (await _run(test_session_factory, run.id)).status == RunStatus.DONE
    assert len([c for c in fake_proposal["calls"] if c["document_id"] == stuck.id]) == max_attempts
    assert len(waits) == max_attempts - 1  # danach kein Warten mehr


@pytest.mark.parametrize("kind", [OllamaErrorKind.UNREACHABLE, OllamaErrorKind.MODEL_MISSING])
async def test_uncapped_wait_kind_keeps_waiting(
    db_session, test_session_factory, settings, fake_proposal, waits, kind,
):
    """Ist die KI ganz weg, liegt es nicht am Dokument: kein Aufgeben nach MAX_WAIT_ATTEMPTS."""
    doc = await _doc(db_session)
    attempts = reanalysis_run_service.MAX_WAIT_ATTEMPTS * 2
    fake_proposal["results"] = {doc.id: [ReanalysisLLMError("weg", kind)] * attempts + ["proposed"]}
    run = await _start(test_session_factory, settings, [doc.id])

    for _ in range(attempts):
        await process_next_item(test_session_factory, settings)
        assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PENDING
    assert (await _run(test_session_factory, run.id)).status == RunStatus.WAITING_AI

    await run_until_idle(test_session_factory, settings)
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PROPOSED
    assert len(waits) == attempts


async def test_wait_attempts_are_counted_per_item(db_session, test_session_factory, settings, fake_proposal, waits):
    """Ein Erfolg zwischendurch setzt den Zaehler des Eintrags zurueck (neuer Eintrag, neue Versuche)."""
    a = await _doc(db_session)
    b = await _doc(db_session)
    timeout = ReanalysisLLMError("zu langsam", OllamaErrorKind.TIMEOUT)
    max_attempts = reanalysis_run_service.MAX_WAIT_ATTEMPTS
    fake_proposal["results"] = {
        a.id: [timeout] * (max_attempts - 1) + ["proposed"],
        b.id: [timeout] * (max_attempts - 1) + ["unchanged"],
    }
    run = await _start(test_session_factory, settings, [a.id, b.id])

    await run_until_idle(test_session_factory, settings)
    assert [i.status for i in await _items(test_session_factory, run.id)] == [
        RunItemStatus.PROPOSED, RunItemStatus.UNCHANGED,
    ]


async def test_wait_for_ai_sleeps_in_slices_and_stops_on_status_change(
    db_session, test_session_factory, settings, no_sleep,
):
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])
    async with test_session_factory() as session:
        stored = await session.get(ReanalysisRun, run.id)
        stored.status = RunStatus.WAITING_AI
        await session.commit()

    await reanalysis_run_service.wait_for_ai(test_session_factory, run.id, 12)
    assert no_sleep == [5, 5, 2]

    no_sleep.clear()
    async with test_session_factory() as session:
        await set_run_status(session, run.id, "pause")  # waehrend des Wartens
        await session.commit()
    await reanalysis_run_service.wait_for_ai(test_session_factory, run.id, 900)
    assert no_sleep == [reanalysis_run_service.IDLE_POLL_S]


async def test_transient_db_error_after_claim_requeues_item(
    db_session, test_session_factory, settings, fake_proposal, monkeypatch,
):
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])
    original = reanalysis_run_service._record_result
    calls = {"n": 0}

    async def flaky_record(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("UPDATE ...", {}, Exception("database is locked"))
        return await original(*args, **kwargs)

    monkeypatch.setattr(reanalysis_run_service, "_record_result", flaky_record)

    assert await process_next_item(test_session_factory, settings) is False
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PENDING

    await run_until_idle(test_session_factory, settings)
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PROPOSED
    assert (await _run(test_session_factory, run.id)).status == RunStatus.DONE


async def test_orphaned_processing_item_is_released(db_session, test_session_factory, settings, fake_proposal):
    """Scheitert auch das Freigeben, raeumt der naechste Aufruf den Eintrag auf."""
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])
    async with test_session_factory() as session:
        item = (await session.execute(select(ReanalysisRunItem))).scalar_one()
        item.status = RunItemStatus.PROCESSING
        await session.commit()

    await run_until_idle(test_session_factory, settings)
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.PROPOSED
    assert (await _run(test_session_factory, run.id)).status == RunStatus.DONE


async def test_requeue_is_conditional_on_active_run(db_session, test_session_factory, settings):
    """Abbruch zwischen Analyse und Vermerk: Eintrag wird SKIPPED, nicht wieder PENDING."""
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])
    async with test_session_factory() as session:
        item = (await session.execute(select(ReanalysisRunItem))).scalar_one()
        item.status = RunItemStatus.PROCESSING
        await session.commit()
        await set_run_status(session, run.id, "cancel")
        await session.commit()
        assert await reanalysis_run_service._requeue_or_skip(session, run.id, item.id) is False
        await session.commit()
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.SKIPPED


async def test_waiting_run_stays_waiting_until_success(db_session, test_session_factory, settings, fake_proposal):
    a = await _doc(db_session)
    gone = await _doc(db_session)
    b = await _doc(db_session)
    fake_proposal["results"] = {a.id: ReanalysisLLMError("weg", OllamaErrorKind.UNREACHABLE)}
    run = await _start(test_session_factory, settings, [a.id, gone.id, b.id])

    await process_next_item(test_session_factory, settings)  # a: KI weg
    fake_proposal["results"] = {}
    gone.status = DocumentStatus.DELETED
    await db_session.commit()
    async with test_session_factory() as session:
        # a ans Ende schieben, damit zuerst das geloeschte Dokument drankommt.
        item = (await session.execute(
            select(ReanalysisRunItem).where(ReanalysisRunItem.document_id == a.id)
        )).scalar_one()
        item.position = 99
        await session.commit()

    await process_next_item(test_session_factory, settings)  # gone: uebersprungen
    assert (await _run(test_session_factory, run.id)).status == RunStatus.WAITING_AI
    await process_next_item(test_session_factory, settings)  # b: Erfolg
    assert (await _run(test_session_factory, run.id)).status == RunStatus.RUNNING


async def test_non_wait_llm_error_fails_item(db_session, test_session_factory, settings, fake_proposal):
    doc = await _doc(db_session)
    fake_proposal["results"] = {doc.id: ReanalysisLLMError("unbrauchbar", OllamaErrorKind.INVALID_RESPONSE)}
    run = await _start(test_session_factory, settings, [doc.id])

    await run_until_idle(test_session_factory, settings)
    item = (await _items(test_session_factory, run.id))[0]
    assert (item.status, item.error) == (RunItemStatus.FAILED, "unbrauchbar")
    assert (await _run(test_session_factory, run.id)).status == RunStatus.DONE


async def test_unexpected_error_fails_item_and_continues(db_session, test_session_factory, settings, fake_proposal):
    a = await _doc(db_session)
    b = await _doc(db_session)
    fake_proposal["results"] = {a.id: RuntimeError("C:\\geheim\\pfad")}
    run = await _start(test_session_factory, settings, [a.id, b.id])

    await run_until_idle(test_session_factory, settings)
    items = await _items(test_session_factory, run.id)
    assert [i.status for i in items] == [RunItemStatus.FAILED, RunItemStatus.PROPOSED]
    assert "geheim" not in items[0].error


async def test_run_skips_deleted_document(db_session, test_session_factory, settings, fake_proposal):
    gone = await _doc(db_session)
    deleted_during = await _doc(db_session)
    ok = await _doc(db_session)
    run = await _start(test_session_factory, settings, [gone.id, deleted_during.id, ok.id])

    gone.status = DocumentStatus.DELETED
    await db_session.commit()

    async def delete_then_fail():
        async with test_session_factory() as session:
            doc = await session.get(type(deleted_during), deleted_during.id)
            doc.status = DocumentStatus.DELETED
            await session.commit()
        raise TaskError(DOCUMENT_GONE_MESSAGE)

    fake_proposal["results"] = {deleted_during.id: delete_then_fail}

    await run_until_idle(test_session_factory, settings)
    items = await _items(test_session_factory, run.id)
    assert [i.status for i in items] == [RunItemStatus.SKIPPED, RunItemStatus.SKIPPED, RunItemStatus.PROPOSED]
    assert items[0].error == DOCUMENT_GONE_MESSAGE
    assert [call["document_id"] for call in fake_proposal["calls"]] == [deleted_during.id, ok.id]

    async with test_session_factory() as session:
        summary = await run_summary(session, await session.get(ReanalysisRun, run.id))
        note = (await session.execute(select(Notification))).scalar_one()
    assert summary.status == RunStatus.DONE
    assert summary.skipped == 2
    assert note.message == "1 Vorschläge, 0 unverändert, 0 Fehler"


async def test_run_skips_document_approved_during_run(db_session, test_session_factory, settings, fake_proposal):
    """REVIEWED wird nicht nur beim Start ausgeschlossen: waehrend des Laufs freigegeben -> SKIPPED."""
    approved = await _doc(db_session)
    ok = await _doc(db_session)
    run = await _start(test_session_factory, settings, [approved.id, ok.id])

    approved.review_status = ReviewStatus.REVIEWED  # nach dem Start freigegeben
    await db_session.commit()

    await run_until_idle(test_session_factory, settings)
    items = await _items(test_session_factory, run.id)
    assert [(i.status, i.error) for i in items] == [
        (RunItemStatus.SKIPPED, reanalysis_run_service.REVIEWED_MEANWHILE_MESSAGE),
        (RunItemStatus.PROPOSED, None),
    ]
    assert "inzwischen freigegeben" in reanalysis_run_service.REVIEWED_MEANWHILE_MESSAGE
    assert [call["document_id"] for call in fake_proposal["calls"]] == [ok.id]
    assert (await _run(test_session_factory, run.id)).status == RunStatus.DONE


async def test_run_recovers_after_restart(db_session, test_session_factory, settings, fake_proposal):
    a = await _doc(db_session)
    b = await _doc(db_session)
    run = await _start(test_session_factory, settings, [a.id, b.id])
    async with test_session_factory() as session:
        item = (await session.execute(
            select(ReanalysisRunItem).where(ReanalysisRunItem.document_id == a.id)
        )).scalar_one()
        item.status = RunItemStatus.PROCESSING  # Absturz mitten in der Analyse
        await session.commit()

    assert await recover_reanalysis_runs(test_session_factory) == 1
    assert [i.status for i in await _items(test_session_factory, run.id)] == [RunItemStatus.PENDING] * 2

    await run_until_idle(test_session_factory, settings)
    finished = await _run(test_session_factory, run.id)
    assert finished.status == RunStatus.DONE
    assert finished.total == 2
    assert [i.status for i in await _items(test_session_factory, run.id)] == [RunItemStatus.PROPOSED] * 2


async def test_cancel_marks_pending_skipped_and_keeps_proposals(db_session, test_session_factory, settings, fake_proposal):
    a = await _doc(db_session)
    b = await _doc(db_session)
    c = await _doc(db_session)
    run = await _start(test_session_factory, settings, [a.id, b.id, c.id])

    async def propose_then_cancel():
        # Vorschlag liegt vor; waehrend der Analyse bricht der Benutzer ab.
        async with test_session_factory() as session:
            session.add(ReanalysisProposal(document_id=b.id, model=MODEL, analysis={}, run_id=run.id))
            await set_run_status(session, run.id, "cancel")
            await session.commit()
        return ProposalOutcome("proposed", [])

    fake_proposal["results"] = {b.id: propose_then_cancel}
    async with test_session_factory() as session:
        session.add(ReanalysisProposal(document_id=a.id, model=MODEL, analysis={}, run_id=run.id))
        await session.commit()

    await run_until_idle(test_session_factory, settings)

    items = await _items(test_session_factory, run.id)
    assert [i.status for i in items] == [RunItemStatus.PROPOSED, RunItemStatus.PROPOSED, RunItemStatus.SKIPPED]
    cancelled = await _run(test_session_factory, run.id)
    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.finished_at is not None
    async with test_session_factory() as session:
        proposals = (await session.execute(select(ReanalysisProposal.document_id))).scalars().all()
        notes = (await session.execute(select(Notification))).scalars().all()
    assert sorted(proposals) == sorted([a.id, b.id])
    assert notes == []
    assert [call["document_id"] for call in fake_proposal["calls"]] == [a.id, b.id]


async def test_wait_error_after_cancel_skips_item(db_session, test_session_factory, settings, fake_proposal):
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])

    async def cancel_then_unreachable():
        async with test_session_factory() as session:
            await set_run_status(session, run.id, "cancel")
            await session.commit()
        raise ReanalysisLLMError("weg", OllamaErrorKind.UNREACHABLE)

    fake_proposal["results"] = {doc.id: cancel_then_unreachable}
    await run_until_idle(test_session_factory, settings)
    assert (await _items(test_session_factory, run.id))[0].status == RunItemStatus.SKIPPED
    assert (await _run(test_session_factory, run.id)).status == RunStatus.CANCELLED


async def test_pause_during_item_finishes_it(db_session, test_session_factory, settings, fake_proposal):
    a = await _doc(db_session)
    b = await _doc(db_session)
    run = await _start(test_session_factory, settings, [a.id, b.id])

    async def pause_then_propose():
        async with test_session_factory() as session:
            await set_run_status(session, run.id, "pause")
            await session.commit()
        return ProposalOutcome("proposed", [])

    fake_proposal["results"] = {a.id: pause_then_propose}
    await run_until_idle(test_session_factory, settings)
    items = await _items(test_session_factory, run.id)
    assert [i.status for i in items] == [RunItemStatus.PROPOSED, RunItemStatus.PENDING]
    assert (await _run(test_session_factory, run.id)).status == RunStatus.PAUSED


async def test_set_run_status_rejects_finished_run(db_session, test_session_factory, settings, fake_proposal):
    doc = await _doc(db_session)
    run = await _start(test_session_factory, settings, [doc.id])
    await run_until_idle(test_session_factory, settings)
    async with test_session_factory() as session:
        for action in ("pause", "resume", "cancel"):
            with pytest.raises(reanalysis_run_service.RunStateError):
                await set_run_status(session, run.id, action)
        with pytest.raises(LookupError):
            await set_run_status(session, "gibt-es-nicht", "pause")
