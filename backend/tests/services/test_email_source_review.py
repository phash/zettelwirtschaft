"""INJ-05: Ein per Prompt-Injection "selbst-zertifiziertes" Analyse-Ergebnis
(confidence 0.99, needs_review=false) darf steuerrelevante E-Mail-Belege
nicht ungeprueft als COMPLETED archivieren."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.document import Document, ReviewStatus
from app.models.processing_job import JobSource, JobStatus
from app.models.review_question import ReviewQuestion
from app.services.analysis_service import AnalysisResult, apply_source_review_policy
from tests.services.test_queue_worker_service import (
    _add_job,
    _get_job_fresh,
    _job_reaches,
    _mock_analyze_success,
    _patch_analyze,
    _run_worker_until,
)


@pytest.fixture(autouse=True)
def _no_vectorize():
    with patch("app.services.vectorize_service.vectorize_document", new_callable=AsyncMock, return_value=0):
        yield


@pytest.fixture
def fast_settings(test_settings: Settings) -> Settings:
    test_settings.QUEUE_POLL_INTERVAL = 0
    return test_settings


def _self_certified(**overrides):
    """Was ein injiziertes Dokument dem Modell diktiert."""
    fields = dict(
        confidence=0.99, needs_review=False, tax_relevant=True,
        tax_category="Werbungskosten", amount=4999.0,
    )
    fields.update(overrides)
    return _mock_analyze_success(**fields)


class TestApplySourceReviewPolicy:
    def test_email_tax_relevant_forces_review(self):
        result = apply_source_review_policy(
            AnalysisResult(confidence=0.99, needs_review=False, tax_relevant=True), JobSource.EMAIL,
        )
        assert result.needs_review is True
        question = result.review_questions[-1]
        assert question["field_affected"] == "tax_relevant"
        assert question["suggested_answers"] == "Ja|Nein"

    @pytest.mark.parametrize("source", [JobSource.UPLOAD, JobSource.WATCH_FOLDER, "UPLOAD", None])
    def test_user_supplied_sources_unchanged(self, source):
        result = apply_source_review_policy(
            AnalysisResult(confidence=0.99, needs_review=False, tax_relevant=True), source,
        )
        assert result.needs_review is False
        assert result.review_questions == []

    def test_email_not_tax_relevant_unchanged(self):
        result = apply_source_review_policy(
            AnalysisResult(confidence=0.99, needs_review=False, tax_relevant=False), "EMAIL",
        )
        assert result.needs_review is False

    def test_none_passthrough(self):
        assert apply_source_review_policy(None, JobSource.EMAIL) is None


class TestWorkerEmailReview:
    async def _run(self, fast_settings, test_session_factory, db_session, sample_pdf, source):
        job_id = await _add_job(db_session, sample_pdf, source=source)
        with _patch_analyze(_self_certified()):
            await _run_worker_until(test_session_factory, fast_settings, _job_reaches(test_session_factory, job_id))
        return await _get_job_fresh(test_session_factory, job_id)

    async def test_email_tax_receipt_lands_in_review(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        job = await self._run(fast_settings, test_session_factory, db_session, sample_pdf, JobSource.EMAIL)
        assert job.status == JobStatus.NEEDS_REVIEW
        async with test_session_factory() as session:
            doc = await session.get(Document, job.document_id)
            questions = (await session.execute(
                select(ReviewQuestion).where(ReviewQuestion.document_id == job.document_id)
            )).scalars().all()
        assert doc.review_status == ReviewStatus.NEEDS_REVIEW
        assert [q.field_affected for q in questions] == ["tax_relevant"]

    async def test_upload_tax_receipt_stays_completed(
        self, fast_settings: Settings, test_session_factory, db_session: AsyncSession, sample_pdf: Path,
    ):
        job = await self._run(fast_settings, test_session_factory, db_session, sample_pdf, JobSource.UPLOAD)
        assert job.status == JobStatus.COMPLETED
