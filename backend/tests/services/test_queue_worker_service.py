import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.services.analysis_service import AnalysisResult
from app.services.ocr_service import OcrResult, PageText
from app.services.queue_worker_service import run_queue_worker


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


def _mock_analyze_success():
    """Mock fuer erfolgreiche Analyse."""
    ocr = OcrResult(
        full_text="Testtext",
        pages=[PageText(page_number=1, text="Testtext", confidence=0.9)],
        average_confidence=0.9,
        page_count=1,
    )
    analysis = AnalysisResult(
        document_type="RECHNUNG",
        confidence=0.92,
        title="Testrechnung",
        needs_review=False,
    )
    return ocr, analysis


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


class TestQueueWorker:
    async def test_processes_pending_job(
        self,
        test_settings: Settings,
        test_session_factory,
        db_session: AsyncSession,
        sample_pdf: Path,
    ):
        """Worker nimmt PENDING-Job auf und setzt Status auf COMPLETED."""
        job = ProcessingJob(
            original_filename="test.pdf",
            stored_filename="abc_test.pdf",
            file_path=str(sample_pdf),
            file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            source=JobSource.UPLOAD,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()
        job_id = job.id

        test_settings.QUEUE_POLL_INTERVAL = 0

        with patch(
            "app.services.queue_worker_service.analyze_document",
            new_callable=AsyncMock,
            return_value=_mock_analyze_success(),
        ):
            task = asyncio.create_task(run_queue_worker(test_session_factory, test_settings))
            await asyncio.sleep(1.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.status == JobStatus.COMPLETED
        assert updated_job.ocr_text == "Testtext"
        assert updated_job.ocr_confidence == 0.9
        assert updated_job.analysis_result is not None
        data = json.loads(updated_job.analysis_result)
        assert data["document_type"] == "RECHNUNG"

    async def test_needs_review_status(
        self,
        test_settings: Settings,
        test_session_factory,
        db_session: AsyncSession,
        sample_pdf: Path,
    ):
        """Job mit NEEDS_REVIEW wird nicht auf COMPLETED ueberschrieben."""
        job = ProcessingJob(
            original_filename="unclear.pdf",
            stored_filename="abc_unclear.pdf",
            file_path=str(sample_pdf),
            file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            source=JobSource.UPLOAD,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()
        job_id = job.id

        test_settings.QUEUE_POLL_INTERVAL = 0

        with patch(
            "app.services.queue_worker_service.analyze_document",
            new_callable=AsyncMock,
            return_value=_mock_analyze_needs_review(),
        ):
            task = asyncio.create_task(run_queue_worker(test_session_factory, test_settings))
            await asyncio.sleep(1.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.status == JobStatus.NEEDS_REVIEW

    async def test_failed_job_retries(
        self,
        test_settings: Settings,
        test_session_factory,
        db_session: AsyncSession,
    ):
        """Worker setzt fehlgeschlagene Jobs zurueck auf PENDING (Retry) und letztlich FAILED."""
        job = ProcessingJob(
            original_filename="missing.pdf",
            stored_filename="abc_missing.pdf",
            file_path="/nonexistent/path/missing.pdf",
            file_type="pdf",
            file_size_bytes=100,
            source=JobSource.UPLOAD,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()
        job_id = job.id

        test_settings.QUEUE_POLL_INTERVAL = 0
        test_settings.MAX_RETRIES = 3
        task = asyncio.create_task(run_queue_worker(test_session_factory, test_settings))
        # Genug Zeit fuer alle 3 Retries
        await asyncio.sleep(1.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        updated_job = await _get_job_fresh(test_session_factory, job_id)
        assert updated_job.retry_count == 3
        assert updated_job.status == JobStatus.FAILED
        assert updated_job.error_message is not None
