"""Aufteilen (Spezifikation 12): Datensicherheit an den Raendern des
Zustandsmodells — unvollstaendiger Seitentext, Retry/Entfernen einzelner
Teil-Jobs, Doppelklick auf "Dokument aufteilen", Fehler beim Anlegen der
Teil-Jobs. OCR und LLM sind gemockt, die PDFs synthetisch.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.document import Document, DocumentStatus
from app.models.processing_job import JobStatus, ProcessingJob
from app.models.split_batch import SplitBatch, SplitBatchStatus
from app.services import split_detection_service, split_service
from app.services.ocr_service import OcrResult
from app.services.split_service import SplitConflictError
from tests.services import test_split_flow as flow
from tests.services.test_pdf_split_service import make_pdf
from tests.services.test_split_flow import (  # noqa: F401 (Fixtures)

    _batch,
    _confirm,
    _detect,
    _job,
    _pipeline_mocks,
    _run_until,
    _upload,
    fast_settings,
    ocr_mock,
)

_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED})


def _full_ocr(_pipeline_mocks, text="Volltext aus der normalen OCR"):
    _pipeline_mocks["extract"].side_effect = None
    _pipeline_mocks["extract"].return_value = OcrResult(full_text=text, average_confidence=0.8, pages=[])


async def _manual_batch(settings, factory, tmp_path):
    doc = await flow.TestManualSplit()._archived(settings, factory, tmp_path)
    async with factory() as session:
        batch, job = await split_service.request_document_split(session, doc, settings)
    await _run_until(factory, settings, [job.id])
    return doc, await _batch(factory, batch.id)


async def _doc(factory, doc_id) -> Document:
    async with factory() as session:
        return await session.get(Document, doc_id)


class TestIncompletePageText:
    async def test_keep_whole_after_time_budget_uses_normal_ocr(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        """Seiten ohne Texterkennung (Zeitbudget) duerfen beim Behalten nicht
        als leer archiviert werden — sonst fehlt ihr Text im Dokument."""
        fast_settings.SPLIT_TIME_BUDGET_S = -1
        job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        async with test_session_factory() as session:
            await split_service.keep_whole(session, batch.id, version=batch.version, settings=fast_settings)
        _full_ocr(_pipeline_mocks)

        await _run_until(test_session_factory, fast_settings, [job_id], _TERMINAL)
        job = await _job(test_session_factory, job_id)
        assert job.document_id, job.error_message
        _pipeline_mocks["extract"].assert_awaited()
        assert "Volltext aus der normalen OCR" in (await _doc(test_session_factory, job.document_id)).ocr_text

    async def test_retry_after_failed_detection_ignores_partial_checkpoint(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        """Erkennung bricht nach dem Checkpoint ab (Stapel FAILED): ein
        erneuter Versuch verarbeitet die ganze Datei — nicht nur die Seiten
        aus dem Checkpoint."""
        job_id = await _upload(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 8))
        async with test_session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            job.status = JobStatus.PROCESSING
            await session.commit()
            await split_detection_service.maybe_detect(job, fast_settings, session, should_pause=lambda: True)
        async with test_session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            assert len(json.loads(job.page_ocr)) == 5  # Checkpoint
            await split_service.transition(
                session, job.split_batch_id, expected=SplitBatchStatus.DETECTING, new=SplitBatchStatus.FAILED,
            )
            job.status = JobStatus.PENDING  # wie POST /api/jobs/{id}/retry nach FAILED
            await session.commit()
        _full_ocr(_pipeline_mocks)

        await _run_until(test_session_factory, fast_settings, [job_id], _TERMINAL)
        job = await _job(test_session_factory, job_id)
        assert job.document_id, job.error_message
        _pipeline_mocks["extract"].assert_awaited()
        assert "Volltext aus der normalen OCR" in (await _doc(test_session_factory, job.document_id)).ocr_text

    def test_load_page_ocr_rejects_skipped_pages(self):
        job = ProcessingJob(page_ocr=json.dumps([
            {"page": 1, "text": "a", "confidence": 1.0, "source": "digital"},
            {"page": 2, "text": "", "confidence": 0.0, "source": "skipped"},
        ]))
        assert split_detection_service.load_page_ocr(job) is None


class TestPartRetryAndDismiss:
    async def _fail_part_two(self, settings, factory, batch, job_ids, *, hold_second=False):
        """Teil 2 scheitert endgueltig (Datei fehlt); optional bleibt Teil 1
        dabei unfertig (PROCESSING)."""
        part2 = split_service.part_path(settings, batch.id, 2)
        moved = part2.with_name("beiseite.bin")
        part2.rename(moved)
        if hold_second:
            async with factory() as session:
                (await session.get(ProcessingJob, job_ids[0])).status = JobStatus.PROCESSING
                await session.commit()
            await _run_until(factory, settings, job_ids[1:], _TERMINAL)
        else:
            await _run_until(factory, settings, job_ids, _TERMINAL)
        assert (await _job(factory, job_ids[1])).status == JobStatus.FAILED
        return part2, moved

    async def test_job_retry_of_failed_part_completes_batch_and_deletes_original(
        self, client, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        """DONE_WITH_ERRORS -> DONE auch, wenn der Teil ueber die Jobliste
        (POST /api/jobs/{id}/retry) wiederholt wird — sonst bleibt das Original
        neben seinen Teilen aktiv."""
        doc, batch = await _manual_batch(fast_settings, test_session_factory, tmp_path)
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1, 2], [3, 4]])
        part2, moved = await self._fail_part_two(fast_settings, test_session_factory, batch, job_ids)
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE_WITH_ERRORS
        assert (await _doc(test_session_factory, doc.id)).status == DocumentStatus.ACTIVE

        moved.rename(part2)
        response = await client.post(f"/api/jobs/{job_ids[1]}/retry")
        assert response.status_code == 200, response.text
        await _run_until(test_session_factory, fast_settings, job_ids, _TERMINAL, files_gone=[part2])

        assert (await _job(test_session_factory, job_ids[1])).status == JobStatus.COMPLETED
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE
        assert (await _doc(test_session_factory, doc.id)).status == DocumentStatus.DELETED

    async def test_dismissed_failed_part_does_not_leave_batch_splitting(
        self, client, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        """Ein entfernter Teil-Job (DELETE /api/jobs) zaehlt als Fehler — der
        Stapel darf nicht bis zum Neustart in SPLITTING haengen."""
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3]])
        await self._fail_part_two(fast_settings, test_session_factory, batch, job_ids, hold_second=True)
        assert (await client.delete(f"/api/jobs/{job_ids[1]}")).status_code == 204

        async with test_session_factory() as session:
            (await session.get(ProcessingJob, job_ids[0])).status = JobStatus.PENDING
            await session.commit()
        await _run_until(test_session_factory, fast_settings, job_ids[:1], _TERMINAL)

        assert (await _job(test_session_factory, job_ids[0])).status == JobStatus.COMPLETED
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE_WITH_ERRORS


class TestManualRequest:
    async def test_concurrent_requests_create_one_batch(self, fast_settings, test_session_factory, tmp_path):
        """Doppelklick auf "Dokument aufteilen": genau ein Stapel, der zweite
        Aufruf bekommt 409."""
        doc = await flow.TestManualSplit()._archived(fast_settings, test_session_factory, tmp_path)

        async def request():
            async with test_session_factory() as session:
                return await split_service.request_document_split(session, doc, fast_settings)

        results = await asyncio.gather(request(), request(), return_exceptions=True)
        assert sum(isinstance(r, SplitConflictError) for r in results) == 1, results
        async with test_session_factory() as session:
            from sqlalchemy import func, select

            count = (await session.execute(
                select(func.count(SplitBatch.id)).where(SplitBatch.source_document_id == doc.id)
            )).scalar_one()
        assert count == 1

    async def test_retry_of_failed_manual_request_does_not_analyse_the_copy(
        self, client, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        doc, batch = await _manual_batch(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            await split_service.transition(
                session, batch.id, expected=SplitBatchStatus.PROPOSED, new=SplitBatchStatus.FAILED,
            )
            job = await session.get(ProcessingJob, batch.source_job_id)
            job.status = JobStatus.FAILED
            await session.commit()
        assert (await client.post(f"/api/jobs/{batch.source_job_id}/retry")).status_code == 200

        await _run_until(test_session_factory, fast_settings, [batch.source_job_id], _TERMINAL)
        job = await _job(test_session_factory, batch.source_job_id)
        assert job.status == JobStatus.FAILED and job.document_id is None
        _pipeline_mocks["llm"].assert_not_awaited()
        assert (await _doc(test_session_factory, doc.id)).status == DocumentStatus.ACTIVE


class TestConfirmFailure:
    async def test_failure_creating_part_jobs_returns_batch_to_proposed(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        with patch.object(split_service, "create_part_jobs", side_effect=RuntimeError("database is locked")):
            with pytest.raises(RuntimeError):
                await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3]])

        batch = await _batch(test_session_factory, batch.id)
        assert batch.status == SplitBatchStatus.PROPOSED
        assert not any((fast_settings.split_dir / batch.id).glob("teil-*"))
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3]])
        assert len(job_ids) == 2
