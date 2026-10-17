import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models.correction_mapping import CorrectionMapping
from app.models.document import Document, DocumentStatus, ReviewStatus
from app.models.email_account import EmailAccount
from app.models.filing_scope import FilingScope
from app.models.notification import Notification, NotificationType
from app.models.processing_job import JOB_ERROR_DUPLICATE, JobSource, JobStatus, ProcessingJob
from app.services import queue_worker_service as worker
from app.services.analysis_service import AnalysisResult
from app.services.archive_service import _compute_file_hash
from app.services.ocr_service import OcrResult, PageText
from app.services.queue_worker_service import (
    _fail_job,
    _process_job,
    recover_interrupted_jobs,
    run_queue_worker,
)
from tests.factories import make_document

_FINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED}


@pytest.fixture(autouse=True)
def _reset_pause_flag():
    worker.resume_queue()
    yield
    worker.resume_queue()


@pytest.fixture(autouse=True)
def _no_vectorize():
    with patch(
        "app.services.vectorize_service.vectorize_document",
        new_callable=AsyncMock,
        return_value=0,
    ) as mock:
        yield mock


@pytest.fixture
def fast_settings(test_settings: Settings) -> Settings:
    test_settings.QUEUE_POLL_INTERVAL = 0
    return test_settings


async def _get_job_fresh(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: str,
) -> ProcessingJob:
    """Holt einen Job mit einer frischen Session (kein Cache)."""
    async with session_factory() as session:
        result = await session.execute(
            select(ProcessingJob).where(ProcessingJob.id == job_id)
        )
        return result.scalar_one()


async def _run_worker_until(session_factory, settings, condition, timeout: float = 10.0) -> None:
    """Startet den Worker und wartet (Polling mit Timeout) bis condition() wahr ist."""
    task = asyncio.create_task(run_queue_worker(session_factory, settings))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while not await condition():
            if task.done():
                raise AssertionError(f"Worker beendet: {task.exception()!r}")
            if loop.time() > deadline:
                raise AssertionError("Timeout: Bedingung nicht erreicht")
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _job_reaches(session_factory, job_id: str, statuses=frozenset(_FINAL_STATUSES)):
    async def condition() -> bool:
        return (await _get_job_fresh(session_factory, job_id)).status in statuses
    return condition


async def _add_job(session: AsyncSession, file_path: Path, **overrides) -> str:
    defaults = dict(
        original_filename=file_path.name,
        stored_filename=f"abc_{file_path.name}",
        file_path=str(file_path),
        file_type=file_path.suffix.lstrip(".") or "pdf",
        file_size_bytes=file_path.stat().st_size if file_path.exists() else 100,
        source=JobSource.UPLOAD,
        status=JobStatus.PENDING,
    )
    defaults.update(overrides)
    job = ProcessingJob(**defaults)
    session.add(job)
    await session.commit()
    return job.id


def _mock_analyze_success(**overrides):
    """Mock fuer erfolgreiche Analyse."""
    ocr = OcrResult(
        full_text="Testtext",
        pages=[PageText(page_number=1, text="Testtext", confidence=0.9)],
        average_confidence=0.9,
        page_count=1,
    )
    fields = dict(
        document_type="RECHNUNG",
        confidence=0.92,
        title="Testrechnung",
        needs_review=False,
    )
    fields.update(overrides)
    return ocr, AnalysisResult(**fields)


def _mock_analyze_needs_review():
    """Mock fuer Analyse mit Review-Bedarf."""
    ocr = OcrResult(
        full_text="Unleserlich",
        pages=[PageText(page_number=1, text="Unleserlich", confidence=0.3)],
        average_confidence=0.3,
        page_count=1,
    )
    analysis = AnalysisResult(
        document_type="SONSTIGES",
        confidence=0.3,
        needs_review=True,
        review_questions=["Bitte pruefen Sie das Dokument."],
    )
    return ocr, analysis


def _patch_analyze(return_value=None, **kwargs):
    return patch(
        "app.services.queue_worker_service.analyze_document",
        new_callable=AsyncMock,
        return_value=return_value if return_value is not None else _mock_analyze_success(),
        **kwargs,
    )


async def _notifications(session_factory) -> list[Notification]:
    async with session_factory() as session:
        return list((await session.execute(select(Notification))).scalars().all())


class TestQueueWorker:
    async def test_processes_pending_job(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """Worker nimmt PENDING-Job auf, setzt COMPLETED und verknuepft das Dokument."""
        job_id = await _add_job(db_session, sample_pdf)

        with _patch_analyze():
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.status == JobStatus.COMPLETED
        assert updated_job.ocr_text == "Testtext"
        assert updated_job.ocr_confidence == 0.9
        data = json.loads(updated_job.analysis_result)
        assert data["document_type"] == "RECHNUNG"
        # Upload-Link: Job zeigt auf das erzeugte Dokument (nicht Job-ID == Dok-ID)
        assert updated_job.document_id is not None
        assert updated_job.document_id != job_id
        async with test_session_factory() as session:
            doc = await session.get(Document, updated_job.document_id)
        assert doc is not None and doc.title == "Testrechnung"
        # Quelldatei nach erfolgreichem Commit entfernt
        assert not sample_pdf.exists()

    async def test_needs_review_status(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """NEEDS_REVIEW-Job: Dokument verknuepft + REVIEW_NEEDED-Benachrichtigung."""
        job_id = await _add_job(db_session, sample_pdf)

        with _patch_analyze(_mock_analyze_needs_review()):
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.status == JobStatus.NEEDS_REVIEW
        assert updated_job.document_id is not None
        notes = await _notifications(test_session_factory)
        assert [n.type for n in notes] == [NotificationType.REVIEW_NEEDED]
        assert notes[0].document_id == updated_job.document_id
        assert "Rückfrage" in notes[0].message

    async def test_scope_only_review_sets_job_needs_review(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """needs_review=False, aber unsichere Scope-Zuordnung -> Job ebenfalls NEEDS_REVIEW."""
        db_session.add(FilingScope(name="Privat", slug="privat", keywords="[]", is_default=True))
        db_session.add(FilingScope(name="Praxis", slug="praxis", keywords="[]"))
        await db_session.commit()
        job_id = await _add_job(db_session, sample_pdf)

        analysis = _mock_analyze_success(filing_scope="Praxis", filing_scope_confidence=0.5)
        with _patch_analyze(analysis):
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.status == JobStatus.NEEDS_REVIEW
        async with test_session_factory() as session:
            doc = await session.get(Document, updated_job.document_id)
        assert doc.review_status == ReviewStatus.NEEDS_REVIEW

    async def test_failed_job_retries(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession,
    ):
        """Worker setzt fehlgeschlagene Jobs zurueck auf PENDING (Retry) und letztlich FAILED."""
        job_id = await _add_job(
            db_session, Path("/nonexistent/path/missing.pdf"), file_size_bytes=100,
        )
        fast_settings.MAX_RETRIES = 3

        await _run_worker_until(
            test_session_factory, fast_settings,
            _job_reaches(test_session_factory, job_id, {JobStatus.FAILED}),
        )

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.retry_count == 3
        assert updated_job.error_message is not None
        notes = await _notifications(test_session_factory)
        assert [n.type for n in notes] == [NotificationType.SYSTEM]
        assert "missing.pdf" in notes[0].message

    async def test_paused_queue_does_not_process(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        job_id = await _add_job(db_session, sample_pdf)
        worker.pause_queue()

        with _patch_analyze() as analyze:
            task = asyncio.create_task(run_queue_worker(test_session_factory, fast_settings))
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert analyze.await_count == 0
        assert (await _get_job_fresh(test_session_factory, job_id)).status == JobStatus.PENDING

    async def test_heartbeat_set_on_claim(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """processing_started_at wird beim Claim/zwischen den Phasen gesetzt."""
        job_id = await _add_job(db_session, sample_pdf)

        with _patch_analyze():
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.processing_started_at is not None
        assert updated_job.status == JobStatus.COMPLETED

    async def test_heartbeat_reset_on_retry(self, test_session_factory, db_session: AsyncSession):
        """Retry-Pfad setzt den Job auf PENDING und den Heartbeat auf None."""
        job_id = await _add_job(
            db_session, Path("/nonexistent/file.pdf"),
            status=JobStatus.PROCESSING,
            processing_started_at=datetime.now(timezone.utc),
        )

        await _fail_job(
            test_session_factory, job_id, "Fehler", count_attempt=True, max_retries=5,
        )

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.status == JobStatus.PENDING
        assert updated_job.retry_count == 1
        assert updated_job.processing_started_at is None

    async def test_heartbeat_does_not_hold_write_lock(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """Waehrend OCR/LLM laeuft, koennen andere Sessions schreiben (kein offener Write-Lock)."""
        job_id = await _add_job(db_session, sample_pdf)
        analysis_started = asyncio.Event()
        release = asyncio.Event()

        async def slow_analyze(*args, **kwargs):
            analysis_started.set()
            await release.wait()
            return _mock_analyze_success()

        other_engine = create_async_engine(
            fast_settings.DATABASE_URL, connect_args={"timeout": 1},
        )
        try:
            with _patch_analyze(side_effect=slow_analyze):
                task = asyncio.create_task(run_queue_worker(test_session_factory, fast_settings))
                await asyncio.wait_for(analysis_started.wait(), timeout=10)
                # Waehrend die "Analyse" laeuft: zweiter Schreiber muss committen koennen.
                async with AsyncSession(other_engine) as other:
                    other.add(ProcessingJob(
                        original_filename="parallel.pdf", stored_filename="p.pdf",
                        file_path="/x/p.pdf", file_type="pdf", file_size_bytes=1,
                        source=JobSource.UPLOAD, status=JobStatus.FAILED,
                    ))
                    await other.commit()
                release.set()
                try:
                    async def done():
                        return (await _get_job_fresh(test_session_factory, job_id)).status in _FINAL_STATUSES
                    for _ in range(500):
                        if await done():
                            break
                        await asyncio.sleep(0.02)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            await other_engine.dispose()

        assert (await _get_job_fresh(test_session_factory, job_id)).status == JobStatus.COMPLETED


class TestDuplicateHandling:
    async def _archived_doc(self, session: AsyncSession, file_path: Path, status=DocumentStatus.ACTIVE) -> Document:
        return await make_document(
            session, commit=True,
            original_filename="alt.pdf", stored_filename="x_alt.pdf", file_path="/archive/alt.pdf",
            file_hash=_compute_file_hash(file_path), title="Stromrechnung Mai", status=status,
        )

    async def test_duplicate_detected_before_analysis(
        self, test_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        existing = await self._archived_doc(db_session, sample_pdf)
        job_id = await _add_job(db_session, sample_pdf)

        with _patch_analyze() as analyze:
            async with test_session_factory() as session:
                job = await session.get(ProcessingJob, job_id)
                with pytest.raises(worker._NonRetryableJobError) as exc_info:
                    await _process_job(job, test_settings, session)

        assert analyze.await_count == 0  # kein OCR/LLM fuer Duplikate
        err = exc_info.value
        assert err.error_code == JOB_ERROR_DUPLICATE
        assert err.document_id == existing.id
        assert err.message == "Diese Datei ist bereits archiviert („Stromrechnung Mai“)."
        assert sample_pdf.exists()  # Quelldatei bleibt fuer den User

    async def test_duplicate_job_failed_once_with_code(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        existing = await self._archived_doc(db_session, sample_pdf)
        job_id = await _add_job(db_session, sample_pdf)
        fast_settings.MAX_RETRIES = 3

        with _patch_analyze():
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        job = await _get_job_fresh(test_session_factory, job_id)
        assert job.status == JobStatus.FAILED
        assert job.error_code == JOB_ERROR_DUPLICATE
        assert job.retry_count == 1  # nicht wiederholt
        assert job.error_message.count("bereits archiviert") == 1
        assert "Duplikat erkannt: Duplikat" not in job.error_message
        # Dashboard verlinkt "Vorhandenes Dokument öffnen" ueber job.document_id
        assert job.document_id == existing.id

    async def test_deleted_document_does_not_block_reimport(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """Entscheidung: erneuter Upload einer geloeschten Datei legt ein NEUES Dokument an."""
        deleted = await self._archived_doc(db_session, sample_pdf, status=DocumentStatus.DELETED)
        job_id = await _add_job(db_session, sample_pdf)

        with _patch_analyze():
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        job = await _get_job_fresh(test_session_factory, job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.document_id not in (None, deleted.id)


class TestAnalysisPostProcessing:
    async def test_auto_apply_correction_replaces_llm_value(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """Entscheidung 13: ab 3x gleicher Korrektur gilt sie deterministisch."""
        db_session.add(CorrectionMapping(
            field="issuer", original_value="Stadtwerke Muster GmbH",
            corrected_value="Stadtwerke Muster", occurrence_count=3, auto_apply=True,
        ))
        await db_session.commit()
        job_id = await _add_job(db_session, sample_pdf)

        with _patch_analyze(_mock_analyze_success(sender="stadtwerke  muster gmbh")):
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        job = await _get_job_fresh(test_session_factory, job_id)
        async with test_session_factory() as session:
            doc = await session.get(Document, job.document_id)
        assert doc.issuer == "Stadtwerke Muster"

    async def test_tax_relevant_without_year_gets_document_year(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        job_id = await _add_job(db_session, sample_pdf)

        with _patch_analyze(_mock_analyze_success(
            tax_relevant=True, tax_year=None, document_date="2024-11-03",
        )):
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        job = await _get_job_fresh(test_session_factory, job_id)
        async with test_session_factory() as session:
            doc = await session.get(Document, job.document_id)
        assert doc.tax_relevant is True
        assert doc.tax_year == 2024


class TestEmailAccountScope:
    async def test_email_job_uses_account_scope(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        privat = FilingScope(name="Privat", slug="privat", keywords='["Testtext"]', is_default=True)
        praxis = FilingScope(name="Praxis", slug="praxis", keywords="[]")
        db_session.add_all([privat, praxis])
        await db_session.flush()
        account = EmailAccount(
            name="Praxis-Postfach", imap_host="imap.example.org", username="u",
            encrypted_password="x", filing_scope_id=praxis.id,
        )
        db_session.add(account)
        await db_session.commit()
        job_id = await _add_job(db_session, sample_pdf, source=JobSource.EMAIL, email_account_id=account.id)

        with _patch_analyze():
            await _run_worker_until(
                test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id),
            )

        job = await _get_job_fresh(test_session_factory, job_id)
        async with test_session_factory() as session:
            doc = await session.get(Document, job.document_id)
        # Keyword "Testtext" wuerde "Privat" waehlen — das Konto uebersteuert.
        assert doc.filing_scope_id == praxis.id
        assert Path(doc.file_path).parts[-5] == "praxis"


class TestCommitFailureCleanup:
    async def test_commit_failure_removes_archive_copy(
        self, test_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        job_id = await _add_job(db_session, sample_pdf)
        archive_dir = Path(test_settings.ARCHIVE_DIR)

        with _patch_analyze():
            async with test_session_factory() as session:
                job = await session.get(ProcessingJob, job_id)
                real_commit = session.commit
                calls = {"n": 0}

                async def failing_commit():
                    calls["n"] += 1
                    if calls["n"] >= 3:  # nach Heartbeats: Abschluss-Commit scheitert
                        raise RuntimeError("Commit fehlgeschlagen")
                    await real_commit()

                with patch.object(session, "commit", side_effect=failing_commit):
                    with pytest.raises(RuntimeError):
                        await _process_job(job, test_settings, session)

        assert [p for p in archive_dir.rglob("*") if p.is_file()] == []
        assert sample_pdf.exists()

    async def test_failure_before_commit_removes_archive_copy(
        self, test_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """Auch ein Fehler zwischen Archivierung und Commit (z.B. Autoflush der
        Fragen-Zaehlung bei NEEDS_REVIEW) hinterlaesst keine verwaiste Kopie."""
        job_id = await _add_job(db_session, sample_pdf)
        archive_dir = Path(test_settings.ARCHIVE_DIR)

        with _patch_analyze(return_value=_mock_analyze_needs_review()), patch(
            "app.services.queue_worker_service._count_open_questions",
            new_callable=AsyncMock, side_effect=RuntimeError("Flush fehlgeschlagen"),
        ):
            async with test_session_factory() as session:
                job = await session.get(ProcessingJob, job_id)
                with pytest.raises(RuntimeError):
                    await _process_job(job, test_settings, session)

        assert [p for p in archive_dir.rglob("*") if p.is_file()] == []
        assert sample_pdf.exists()


class TestRecoverInterruptedJobs:
    async def test_recently_claimed_job_is_reset(
        self, test_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        """Auch ein erst vor 1 Minute geclaimter Job wird nach dem Neustart zurueckgesetzt."""
        job_id = await _add_job(
            db_session, sample_pdf, status=JobStatus.PROCESSING,
            processing_started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        async with test_session_factory() as session:
            assert await recover_interrupted_jobs(session, test_settings) == 1

        job = await _get_job_fresh(test_session_factory, job_id)
        assert job.status == JobStatus.PENDING
        assert job.processing_started_at is None
        assert job.retry_count == 1

    async def test_poison_pill_fails_after_max_retries(
        self, test_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        test_settings.MAX_RETRIES = 3
        job_id = await _add_job(
            db_session, sample_pdf, status=JobStatus.PROCESSING, retry_count=2,
        )

        async with test_session_factory() as session:
            await recover_interrupted_jobs(session, test_settings)

        job = await _get_job_fresh(test_session_factory, job_id)
        assert job.status == JobStatus.FAILED
        assert job.retry_count == 3
        notes = await _notifications(test_session_factory)
        assert [n.type for n in notes] == [NotificationType.SYSTEM]

    async def test_other_statuses_untouched(
        self, test_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        job_id = await _add_job(db_session, sample_pdf, status=JobStatus.PENDING)
        async with test_session_factory() as session:
            assert await recover_interrupted_jobs(session, test_settings) == 0
        assert (await _get_job_fresh(test_session_factory, job_id)).retry_count == 0
