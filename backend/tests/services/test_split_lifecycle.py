"""Lebenszyklus aufgeteilter Stapel (Spezifikation 12, Etappe 3): "Aufteilung
rückgängig" inkl. Bearbeitungssperre, Aufbewahrung/Aufraeumen, gekuerzte
Kopfzeilen in terminalen Zustaenden.

OCR und LLM sind gemockt, die PDFs synthetisch (reportlab) — keine echten
Scans, keine Personendaten.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text, update

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus
from app.models.processing_job import JobSource, JobStatus, ProcessingJob, SplitRole
from app.models.split_batch import SplitBatch, SplitBatchStatus, SplitOrigin
from app.services import split_service
from app.services.split_service import SplitConflictError
from tests.services.test_pdf_split_service import make_pdf
from tests.services.test_split_flow import (  # noqa: F401 — Fixtures
    TestManualSplit,
    _batch,
    _confirm,
    _detect,
    _job,
    _pipeline_mocks,
    _run_until,
    fast_settings,
    ocr_mock,
)


async def _split_done(settings, factory, tmp_path, parts=([1, 2], [3, 4])) -> tuple[str, SplitBatch, list[str]]:
    """AUTO-Stapel (Upload-Option) erkennen, aufteilen, alle Teile archivieren."""
    job_id, batch = await _detect(settings, factory, make_pdf(tmp_path / "stapel.pdf", 4))
    job_ids = await _confirm(factory, settings, batch, [list(p) for p in parts])
    await _run_until(factory, settings, job_ids)
    batch = await _batch(factory, batch.id)
    assert batch.status == SplitBatchStatus.DONE
    return job_id, batch, job_ids


async def _parts(factory, batch_id) -> list[Document]:
    async with factory() as session:
        return list((await session.execute(
            select(Document).where(Document.split_batch_id == batch_id).order_by(Document.split_part_index)
        )).scalars().all())


async def _undo(factory, settings, batch: SplitBatch):
    async with factory() as session:
        return await split_service.undo(session, batch.id, version=batch.version, settings=settings)


async def _fts_ids(factory) -> set[str]:
    async with factory() as session:
        return {row[0] for row in (await session.execute(text("SELECT doc_id FROM documents_fts"))).all()}


class TestUndoAuto:
    async def test_undo_returns_last_split_as_proposal(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        job_id, batch, part_job_ids = await _split_done(fast_settings, test_session_factory, tmp_path)
        parts = await _parts(test_session_factory, batch.id)
        assert len(parts) == 2 and all(p.status != DocumentStatus.DELETED for p in parts)
        assert {p.id for p in parts} <= await _fts_ids(test_session_factory)

        result = await _undo(test_session_factory, fast_settings, batch)
        assert sorted(result.removed_document_ids) == sorted(p.id for p in parts)
        assert result.restored_document_id is None

        batch = await _batch(test_session_factory, batch.id)
        assert batch.status == SplitBatchStatus.PROPOSED and batch.finished_at is None
        cuts = [b["after_page"] for b in json.loads(batch.proposal)["boundaries"] if b["decision"] == "cut"]
        assert cuts == [2]  # letzte Aufteilung als Vorschlag
        assert json.loads(batch.final_parts)["parts_written"] is False
        assert (await _job(test_session_factory, job_id)).status == JobStatus.AWAITING_SPLIT
        for part_job_id in part_job_ids:
            assert await _job(test_session_factory, part_job_id) is None  # Teil-Jobs entfernt
        parts = await _parts(test_session_factory, batch.id)
        assert all(p.status == DocumentStatus.DELETED for p in parts)
        assert not {p.id for p in parts} & await _fts_ids(test_session_factory)
        async with test_session_factory() as session:
            undone = (await session.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.SPLIT_UNDONE)
            )).scalars().all()
        assert {a.document_id for a in undone} == {p.id for p in parts}
        assert Path(batch.file_path).is_file()  # Quelle bleibt fuer den neuen Vorschlag

        # Erneut (anders) aufteilen: Unique-Indizes blockieren nicht
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3, 4]])
        await _run_until(test_session_factory, fast_settings, job_ids)
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE
        active = [p for p in await _parts(test_session_factory, batch.id) if p.status != DocumentStatus.DELETED]
        assert [p.source_pages for p in active] == ["1", "2-4"]
        assert sorted(ocr_mock.calls) == [1, 2, 3, 4]  # auch nach "rückgängig" keine zweite OCR

    async def test_undo_is_blocked_after_a_user_edit(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        _, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path)
        parts = await _parts(test_session_factory, batch.id)
        async with test_session_factory() as session:
            assert await split_service.undo_blocker(session, batch) is None
            # Systemeintraege (CREATED, SPLIT_CREATED) sperren nicht ...
            session.add(AuditLog(document_id=parts[1].id, action=AuditAction.REVIEWED, details="{}"))
            await session.commit()
            reason = await split_service.undo_blocker(session, batch)
        assert reason and "bearbeitet" in reason
        with pytest.raises(SplitConflictError, match="bearbeitet"):
            await _undo(test_session_factory, fast_settings, batch)
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE

    @pytest.mark.parametrize("action", [AuditAction.UPDATED, AuditAction.TAG_ADDED, AuditAction.TAG_REMOVED])
    async def test_every_user_edit_blocks(self, fast_settings, test_session_factory, tmp_path, ocr_mock, action):
        _, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path, parts=([1, 2, 3, 4],))
        [part] = await _parts(test_session_factory, batch.id)
        async with test_session_factory() as session:
            session.add(AuditLog(document_id=part.id, action=action, details="{}"))
            await session.commit()
            assert await split_service.undo_blocker(session, batch)

    async def test_undo_needs_the_kept_source(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        _, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path)
        Path(batch.file_path).unlink()
        async with test_session_factory() as session:
            assert "nicht mehr aufbewahrt" in await split_service.undo_blocker(session, batch)
        with pytest.raises(SplitConflictError):
            await _undo(test_session_factory, fast_settings, batch)

    async def test_double_undo_and_stale_version(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        _, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path)
        with pytest.raises(SplitConflictError, match="inzwischen geändert"):
            async with test_session_factory() as session:
                await split_service.undo(session, batch.id, version=batch.version - 1, settings=fast_settings)
        await _undo(test_session_factory, fast_settings, batch)
        with pytest.raises(SplitConflictError):
            await _undo(test_session_factory, fast_settings, batch)  # alte Version, jetzt PROPOSED

    async def test_undo_recreates_a_removed_source_job(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        job_id, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            await session.delete(await session.get(ProcessingJob, job_id))  # DELETE /api/jobs bei SPLIT
            await session.commit()
        await _undo(test_session_factory, fast_settings, batch)
        batch = await _batch(test_session_factory, batch.id)
        assert batch.source_job_id != job_id
        job = await _job(test_session_factory, batch.source_job_id)
        assert job.status == JobStatus.AWAITING_SPLIT and job.split_role == SplitRole.SOURCE.value
        assert job.file_path == batch.file_path

    async def test_undo_with_failed_parts(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        _, batch, job_ids = await _split_done(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            await session.execute(update(ProcessingJob).where(ProcessingJob.id == job_ids[1]).values(
                status=JobStatus.FAILED, document_id=None,
            ))
            await session.execute(update(SplitBatch).where(SplitBatch.id == batch.id).values(
                status=SplitBatchStatus.DONE_WITH_ERRORS,
            ))
            await session.commit()
        batch = await _batch(test_session_factory, batch.id)
        await _undo(test_session_factory, fast_settings, batch)
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.PROPOSED

    @pytest.mark.parametrize("job_status", [JobStatus.PENDING, JobStatus.PROCESSING])
    async def test_undo_blocked_while_a_part_is_still_processed(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, job_status,
    ):
        # DONE_WITH_ERRORS + Teil ueber POST /api/jobs/{id}/retry neu eingereiht:
        # "rückgängig" darf den laufenden Teil-Job nicht loeschen (sonst
        # archivierte der Worker ein aktives Teildokument ohne Job/Stapel-Bezug).
        _, batch, job_ids = await _split_done(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            await session.execute(update(ProcessingJob).where(ProcessingJob.id == job_ids[1]).values(
                status=job_status,
            ))
            await session.execute(update(SplitBatch).where(SplitBatch.id == batch.id).values(
                status=SplitBatchStatus.DONE_WITH_ERRORS,
            ))
            await session.commit()
        batch = await _batch(test_session_factory, batch.id)
        async with test_session_factory() as session:
            assert "verarbeitet" in (await split_service.undo_blocker(session, batch) or "")
        with pytest.raises(SplitConflictError):
            await _undo(test_session_factory, fast_settings, batch)
        assert await _job(test_session_factory, job_ids[1]) is not None
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE_WITH_ERRORS


class TestUndoManual:
    async def test_undo_restores_the_original(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        doc = await TestManualSplit()._archived(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            batch, job = await split_service.request_document_split(session, doc, fast_settings)
        await _run_until(test_session_factory, fast_settings, [job.id])
        batch = await _batch(test_session_factory, batch.id)
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1, 2], [3, 4]])
        await _run_until(test_session_factory, fast_settings, job_ids)
        batch = await _batch(test_session_factory, batch.id)
        async with test_session_factory() as session:
            assert (await session.get(Document, doc.id)).status == DocumentStatus.DELETED
            split_audit = (await session.execute(
                select(AuditLog).where(AuditLog.document_id == doc.id, AuditLog.action == AuditAction.SPLIT)
            )).scalar_one()
        assert json.loads(split_audit.details)["previous_status"] == "ACTIVE"

        result = await _undo(test_session_factory, fast_settings, batch)
        assert result.restored_document_id == doc.id
        batch = await _batch(test_session_factory, batch.id)
        assert batch.status == SplitBatchStatus.UNDONE
        async with test_session_factory() as session:
            original = await session.get(Document, doc.id)
            assert original.status == DocumentStatus.ACTIVE
            assert await session.get(ProcessingJob, job.id) is None  # Stapel-Job weg
        assert all(p.status == DocumentStatus.DELETED for p in await _parts(test_session_factory, batch.id))
        assert doc.id in await _fts_ids(test_session_factory)
        assert not Path(batch.file_path).exists()  # Kopie + Vorschaubilder weg
        assert not split_service.batch_thumbnail_dir(fast_settings, batch.id).exists()
        # Original kann erneut aufgeteilt werden
        async with test_session_factory() as session:
            assert await split_service.active_batch_for_document(session, doc.id) is None

    async def test_undo_blocked_when_original_is_gone_or_reimported(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        doc = await TestManualSplit()._archived(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            batch, job = await split_service.request_document_split(session, doc, fast_settings)
        await _run_until(test_session_factory, fast_settings, [job.id])
        batch = await _batch(test_session_factory, batch.id)
        await _run_until(test_session_factory, fast_settings,
                         await _confirm(test_session_factory, fast_settings, batch, [[1, 2, 3, 4]]))
        batch = await _batch(test_session_factory, batch.id)
        from tests.factories import make_document

        async with test_session_factory() as session:
            await make_document(session, original_filename="kopie.pdf", file_hash=doc.file_hash)
            await session.commit()
            assert "erneut archiviert" in await split_service.undo_blocker(session, batch)
            row = await session.get(SplitBatch, batch.id)
            row.source_document_id = "00000000-0000-4000-8000-000000000000"
            await session.commit()
            assert "nicht mehr vorhanden" in await split_service.undo_blocker(session, row)


class TestRetentionAndCleanup:
    async def _age(self, factory, batch_id, days: int, status: SplitBatchStatus | None = None):
        values = {"finished_at": datetime.now(timezone.utc) - timedelta(days=days)}
        if status is not None:
            values["status"] = status
        async with factory() as session:
            await session.execute(update(SplitBatch).where(SplitBatch.id == batch_id).values(**values))
            await session.commit()

    async def test_done_batch_files_removed_after_retention(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        job_id, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path)
        thumbs = split_service.batch_thumbnail_dir(fast_settings, batch.id)
        assert Path(batch.file_path).is_file() and thumbs.is_dir()
        assert (await _job(test_session_factory, job_id)).page_ocr  # fuer "rückgängig" aufbewahrt

        await self._age(test_session_factory, batch.id, 29)
        async with test_session_factory() as session:
            assert await split_service.cleanup_expired(session, fast_settings) == 0
        assert Path(batch.file_path).is_file()

        await self._age(test_session_factory, batch.id, 31)
        async with test_session_factory() as session:
            assert await split_service.cleanup_expired(session, fast_settings) == 1
            assert await split_service.cleanup_expired(session, fast_settings) == 0  # idempotent
            row = await session.get(SplitBatch, batch.id)
            assert "nicht mehr aufbewahrt" in await split_service.undo_blocker(session, row)
        assert not Path(batch.file_path).exists() and not thumbs.exists()
        assert (await _job(test_session_factory, job_id)).page_ocr is None  # keine dauerhafte Textkopie
        # Teildokumente sind unberuehrt
        assert all(Path(p.file_path).is_file() for p in await _parts(test_session_factory, batch.id))

    async def test_keep_days_is_configurable(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        fast_settings.SPLIT_KEEP_SOURCE_DAYS = 0
        _, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path)
        await self._age(test_session_factory, batch.id, 0)
        async with test_session_factory() as session:
            assert await split_service.cleanup_expired(session, fast_settings) == 1

    @pytest.mark.parametrize("status", [
        SplitBatchStatus.PROPOSED, SplitBatchStatus.SPLITTING, SplitBatchStatus.DETECTING,
        SplitBatchStatus.DONE_WITH_ERRORS,
    ])
    async def test_active_and_retryable_batches_are_never_touched(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, status,
    ):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        await self._age(test_session_factory, batch.id, 400, status)
        async with test_session_factory() as session:
            assert await split_service.cleanup_expired(session, fast_settings) == 0
            assert split_service.is_protected(batch.file_path, await split_service.protected_source_paths(session)) \
                or status == SplitBatchStatus.DONE_WITH_ERRORS
        assert Path(batch.file_path).is_file()

    async def test_cleanup_skips_a_batch_that_changed_meanwhile(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, monkeypatch,
    ):
        # Wettlauf: zwischen Auswahl und Loeschen macht "rückgängig" den Stapel
        # wieder zum Vorschlag — seine Quelle darf dann nicht geloescht werden.
        _, batch, _ = await _split_done(fast_settings, test_session_factory, tmp_path)
        await self._age(test_session_factory, batch.id, 31)
        original = split_service._source_job

        async def undo_meanwhile(session, row):
            async with test_session_factory() as other:
                await other.execute(update(SplitBatch).where(SplitBatch.id == batch.id).values(
                    status=SplitBatchStatus.PROPOSED, version=SplitBatch.version + 1,
                ))
                await other.commit()
            return await original(session, row)

        monkeypatch.setattr(split_service, "_source_job", undo_meanwhile)
        async with test_session_factory() as session:
            assert await split_service.cleanup_expired(session, fast_settings) == 0
        assert Path(batch.file_path).is_file()

    async def test_file_still_needed_by_the_job_is_kept(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        await self._age(test_session_factory, batch.id, 400, SplitBatchStatus.KEPT_WHOLE)
        for status, source, kept in [
            (JobStatus.PENDING, JobSource.UPLOAD, True),  # "Als ein Dokument behalten", wartet noch
            (JobStatus.FAILED, JobSource.UPLOAD, True),  # kann wiederholt werden
            (JobStatus.FAILED, JobSource.SPLIT_REQUEST, False),  # nur Kopie eines Archivdokuments
        ]:
            async with test_session_factory() as session:
                await session.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(
                    status=status, source=source,
                ))
                await session.commit()
                await split_service.cleanup_expired(session, fast_settings)
            assert Path(batch.file_path).is_file() is kept, (status, source)


class TestHeaderTrimming:
    async def test_terminal_states_trim_header_hashes(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        pages = json.loads(batch.pages)
        assert any(p["sig"]["header"] for p in pages)  # waehrend der Pruefung vorhanden
        async with test_session_factory() as session:
            await split_service.keep_whole(session, batch.id, version=batch.version, settings=fast_settings)
        trimmed = json.loads((await _batch(test_session_factory, batch.id)).pages)
        assert all(p["sig"]["header"] == [] for p in trimmed)
        assert [p["sig"]["counter"] for p in trimmed] == [p["sig"]["counter"] for p in pages]  # Rest bleibt

    def test_trim_tolerates_missing_or_broken_json(self):
        assert split_service.trim_headers(None) is None
        assert split_service.trim_headers("kaputt") == "kaputt"
        assert json.loads(split_service.trim_headers('[{"n": 1, "sig": null}]')) == [{"n": 1, "sig": None}]


def test_origin_enum_values_unchanged():
    assert SplitOrigin.AUTO.value == "AUTO" and SplitOrigin.MANUAL.value == "MANUAL"


async def test_cleanup_task_runs_at_start_and_survives_errors(test_settings):
    """Hintergrund-Task: sofort ein Lauf, Fehler beenden die Schleife nicht."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    calls = []

    async def fake_cleanup(session, settings):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Platte voll")
        return 0

    sleeps = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    with patch.object(split_service, "cleanup_expired", side_effect=fake_cleanup), \
            patch("app.services.split_service.asyncio.sleep", sleeps):
        with pytest.raises(asyncio.CancelledError):
            await split_service.run_split_cleanup(_Factory(), test_settings)
    assert len(calls) == 2
    assert sleeps.await_args_list[0].args == (split_service.CLEANUP_INTERVAL_SECONDS,)
