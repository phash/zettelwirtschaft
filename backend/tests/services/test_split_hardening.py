"""Aufteilen (Spezifikation 12, Etappe 4): Haertung der Sonderfaelle.

E-Mail-/Watch-Ordner-Quellen (inkl. geloeschtem Konto), Abbruch beim
Schreiben der Teile, Wiederholen fehlgeschlagener Teile, verwaiste
Stapel-Ordner nach einem Absturz, Duplikat eines wartenden Stapels,
verschluesselte/zu grosse PDFs und gleichzeitige Bearbeitung (409).

OCR und LLM sind gemockt, die PDFs synthetisch (reportlab) — keine echten
Scans, keine Personendaten.
"""

import asyncio
import os
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select

from app.models.document import Document
from app.models.email_account import EmailAccount
from app.models.filing_scope import FilingScope
from app.models.notification import Notification
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.models.processing_job import (
    JOB_ERROR_DUPLICATE,
    JobSource,
    JobStatus,
    ProcessingJob,
    SplitMode,
)
from app.models.split_batch import SplitBatchStatus
from app.services import pdf_split_service, split_service
from app.services.ocr_service import OcrResult
from app.services.pdf_split_service import PdfSplitError
from app.services.split_service import SplitConflictError, SplitValidationError
from tests.services import test_split_flow as flow
from tests.services.test_pdf_split_service import make_pdf
from tests.services.test_split_flow import (
    _batch,
    _confirm,
    _detect,
    _job,
    _run_until,
    _upload,
)

pytestmark = pytest.mark.usefixtures("_pipeline_mocks")

_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED})


async def _email_account(factory, scope_name="Praxis") -> tuple[int, str]:
    async with factory() as session:
        scope = FilingScope(name=scope_name, slug=scope_name.lower(), keywords="[]")
        session.add(scope)
        await session.flush()
        account = EmailAccount(
            name="Postfach", imap_host="imap.example.org", username="u",
            encrypted_password="x", filing_scope_id=scope.id,
        )
        session.add(account)
        await session.commit()
        return account.id, scope.id


async def _part_documents(factory, batch_id) -> list[Document]:
    async with factory() as session:
        return list((await session.execute(
            select(Document).where(Document.split_batch_id == batch_id).order_by(Document.split_part_index)
        )).scalars().all())


class TestEmailAndWatchFolderSources:
    async def test_email_parts_inherit_account_and_its_filing_scope(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        account_id, scope_id = await _email_account(test_session_factory)
        job_id, batch = await _detect(
            fast_settings, test_session_factory, make_pdf(tmp_path / "anhang.pdf", 3),
            source=JobSource.EMAIL, email_account_id=account_id,
        )
        async with test_session_factory() as session:
            session.add(ProcessedEmail(
                email_account_id=account_id, message_id="<m1@example.org>",
                status=EmailStatus.RELEVANT, processing_job_id=job_id,
            ))
            await session.commit()
        assert batch.email_account_id == account_id and batch.job_source == "EMAIL"

        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3]])
        parts = [await _job(test_session_factory, i) for i in job_ids]
        assert all(p.source == JobSource.EMAIL and p.email_account_id == account_id for p in parts)
        await _run_until(test_session_factory, fast_settings, job_ids)

        docs = await _part_documents(test_session_factory, batch.id)
        assert len(docs) == 2 and all(d.filing_scope_id == scope_id for d in docs)
        async with test_session_factory() as session:
            mail = (await session.execute(select(ProcessedEmail))).scalar_one()
        assert mail.processing_job_id == job_id  # zeigt weiter auf den Stapel-Job

    async def test_deleted_account_while_waiting_does_not_block_confirm(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        """Das Konto wird geloescht, waehrend der Stapel wartet: die Teile
        duerfen die verwaiste Konto-ID nicht erben (FK-Verletzung -> das
        Bestaetigen scheiterte dauerhaft)."""
        account_id, _ = await _email_account(test_session_factory)
        job_id, batch = await _detect(
            fast_settings, test_session_factory, make_pdf(tmp_path / "anhang.pdf", 2),
            source=JobSource.EMAIL, email_account_id=account_id,
        )
        async with test_session_factory() as session:
            await session.execute(delete(EmailAccount).where(EmailAccount.id == account_id))
            await session.commit()
        assert (await _job(test_session_factory, job_id)).email_account_id is None  # FK SET NULL

        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2]])
        parts = [await _job(test_session_factory, i) for i in job_ids]
        assert [p.email_account_id for p in parts] == [None, None]
        await _run_until(test_session_factory, fast_settings, job_ids)
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE

    async def test_discarding_an_email_stack_keeps_the_mail_record(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        account_id, _ = await _email_account(test_session_factory)
        job_id, batch = await _detect(
            fast_settings, test_session_factory, make_pdf(tmp_path / "anhang.pdf", 2),
            source=JobSource.EMAIL, email_account_id=account_id,
        )
        async with test_session_factory() as session:
            session.add(ProcessedEmail(
                email_account_id=account_id, message_id="<m2@example.org>",
                status=EmailStatus.RELEVANT, processing_job_id=job_id,
            ))
            await session.commit()
            await split_service.discard(session, batch.id, version=batch.version, settings=fast_settings)
        async with test_session_factory() as session:
            mail = (await session.execute(select(ProcessedEmail))).scalar_one()
        assert mail.processing_job_id is None  # ON DELETE SET NULL, kein FK-Fehler

    async def test_watch_folder_parts_keep_their_source(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        _, batch = await _detect(
            fast_settings, test_session_factory, make_pdf(tmp_path / "scan.pdf", 2), source=JobSource.WATCH_FOLDER,
        )
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2]])
        parts = [await _job(test_session_factory, i) for i in job_ids]
        assert [p.source for p in parts] == [JobSource.WATCH_FOLDER] * 2
        assert all(p.email_account_id is None for p in parts)


class TestPartsAreIndexed:
    async def test_every_part_is_fts_and_vector_indexed_with_provenance(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        """Akzeptanzkriterium: jeder Teil ist ein eigenes, FTS- und
        vektorindiziertes Dokument mit source_pages + AuditLog SPLIT_CREATED."""
        from sqlalchemy import text

        from app.models.audit_log import AuditAction, AuditLog

        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 4))
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1, 2], [3, 4]])
        with patch(
            "app.services.vectorize_service.vectorize_document", new_callable=AsyncMock, return_value=3,
        ) as vectorize:
            await _run_until(test_session_factory, fast_settings, job_ids)

        docs = await _part_documents(test_session_factory, batch.id)
        assert [d.source_pages for d in docs] == ["1-2", "3-4"]
        async with test_session_factory() as session:
            fts = {row[0] for row in (await session.execute(text("SELECT doc_id FROM documents_fts"))).all()}
            created = (await session.execute(
                select(AuditLog.document_id).where(AuditLog.action == AuditAction.SPLIT_CREATED)
            )).scalars().all()
        assert {d.id for d in docs} <= fts
        assert sorted(created) == sorted(d.id for d in docs)
        assert sorted(call.args[0].id for call in vectorize.await_args_list) == sorted(d.id for d in docs)


class TestCrashAndPartialFailures:
    async def test_write_failure_of_a_later_part_leaves_no_part_files(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        """Abbruch mitten im Schreiben (Teil 2 von 3): Stapel wieder PROPOSED,
        keine halben Teildateien, keine Teil-Jobs — erneutes Bestaetigen geht."""
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        real_write = pdf_split_service.write_part

        def flaky(src, pages, dst):
            if dst.name == "teil-02.pdf":
                raise PdfSplitError("Datenträger voll")
            return real_write(src, pages, dst)

        with patch.object(pdf_split_service, "write_part", side_effect=flaky):
            with pytest.raises(SplitValidationError):
                await _confirm(test_session_factory, fast_settings, batch, [[1], [2], [3]])

        reverted = await _batch(test_session_factory, batch.id)
        assert reverted.status == SplitBatchStatus.PROPOSED
        assert reverted.error_message == "Die Teile konnten nicht geschrieben werden."
        assert not list((fast_settings.split_dir / batch.id).glob("teil-*"))
        async with test_session_factory() as session:
            assert await split_service.part_jobs(session, batch.id) == []

        job_ids = await _confirm(test_session_factory, fast_settings, reverted, [[1], [2], [3]])
        assert len(job_ids) == 3

    async def test_retry_failed_parts_completes_the_batch(
        self, client, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3]])
        part2 = split_service.part_path(fast_settings, batch.id, 2)
        moved = part2.with_name("beiseite.bin")
        part2.rename(moved)
        await _run_until(test_session_factory, fast_settings, job_ids, _TERMINAL)
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE_WITH_ERRORS

        # Datei fehlt noch: nichts einzureihen
        assert (await client.post(f"/api/split/{batch.id}/retry-failed")).status_code == 409
        moved.rename(part2)
        response = await client.post(f"/api/split/{batch.id}/retry-failed")
        assert response.status_code == 200, response.text
        assert response.json()["job_ids"] == [job_ids[1]]
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.SPLITTING

        await _run_until(test_session_factory, fast_settings, job_ids, _TERMINAL, files_gone=[part2])
        assert (await _job(test_session_factory, job_ids[1])).status == JobStatus.COMPLETED
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE
        assert len(await _part_documents(test_session_factory, batch.id)) == 2
        # Abgeschlossen: kein zweites Wiederholen
        assert (await client.post(f"/api/split/{batch.id}/retry-failed")).status_code == 409

    async def test_orphan_batch_dirs_after_a_crash_are_removed(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        """Absturz zwischen dem Kopieren der Quelle und dem Commit hinterlaesst
        ``data/split/<id>/`` ohne Stapel-Zeile. Der Wartungslauf entfernt nur
        alte, unbekannte Ordner — nie die eines Stapels oder eines Jobs."""
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 2))
        old = time.time() - 3 * 86400

        def make_dir(root: Path, name: str, *, age: float | None = old) -> Path:
            directory = root / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "source.pdf").write_bytes(b"%PDF-")
            if age is not None:
                os.utime(directory, (age, age))
            return directory

        orphan = make_dir(fast_settings.split_dir, str(uuid.uuid4()))
        orphan_thumbs = make_dir(fast_settings.split_thumbnail_dir, str(uuid.uuid4()))
        young = make_dir(fast_settings.split_dir, str(uuid.uuid4()), age=None)
        not_a_batch = make_dir(fast_settings.split_dir, "eigene-dateien")
        # AUTO "ein Dokument": Stapel-Zeile geloescht, Job zeigt noch in den Ordner
        in_use = make_dir(fast_settings.split_dir, str(uuid.uuid4()))
        async with test_session_factory() as session:
            session.add(ProcessingJob(
                original_filename="x.pdf", stored_filename="x.pdf", file_path=str(in_use / "source.pdf"),
                file_type="pdf", file_size_bytes=1, source=JobSource.UPLOAD, status=JobStatus.FAILED,
            ))
            await session.commit()
        os.utime(fast_settings.split_dir / batch.id, (old, old))

        async with test_session_factory() as session:
            assert await split_service.cleanup_expired(session, fast_settings) == 0
        assert not orphan.exists() and not orphan_thumbs.exists()
        assert young.exists() and not_a_batch.exists() and in_use.exists()
        assert Path(batch.file_path).is_file()

    async def test_orphan_sweep_keeps_dirs_of_jobs_stored_under_another_root(
        self, fast_settings, test_session_factory, tmp_path,
    ):
        """Gespeicherte Pfade sind absolut. Zeigt ein Job auf denselben
        Stapel-Ordner unter einem anderen Wurzelpfad (Datenordner verschoben,
        Docker -> Native), darf der Wartungslauf den Ordner nicht loeschen —
        rmtree nur, wenn sicher nichts mehr darauf verweist."""
        old = time.time() - 3 * 86400
        name = str(uuid.uuid4())
        directory = fast_settings.split_dir / name
        directory.mkdir(parents=True)
        (directory / "source.pdf").write_bytes(b"%PDF-")
        os.utime(directory, (old, old))
        stored = tmp_path / "alter-datenordner" / "split" / name / "source.pdf"
        async with test_session_factory() as session:
            session.add(ProcessingJob(
                original_filename="x.pdf", stored_filename="x.pdf", file_path=str(stored),
                file_type="pdf", file_size_bytes=1, source=JobSource.UPLOAD, status=JobStatus.FAILED,
            ))
            await session.commit()

        async with test_session_factory() as session:
            assert await split_service.remove_orphan_batch_dirs(session, fast_settings) == 0
        assert (directory / "source.pdf").is_file()


class TestDuplicatesAndBrokenFiles:
    async def test_same_stack_while_waiting_is_a_duplicate(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        pdf = make_pdf(tmp_path / "s.pdf", 2)
        _, batch = await _detect(fast_settings, test_session_factory, pdf)
        job_id = await _upload(fast_settings, test_session_factory, pdf, split_mode=SplitMode.AUTO)
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.FAILED and job.error_code == JOB_ERROR_DUPLICATE
        assert "wartet bereits auf die Aufteilung" in job.error_message
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.PROPOSED

    async def test_forced_split_of_encrypted_pdf_reports_and_runs_normally(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = OcrResult(full_text="Text", average_confidence=0.8, pages=[])
        job_id = await _upload(fast_settings, test_session_factory, make_pdf(tmp_path / "e.pdf", 3, encrypt=True))
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.status in (JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW), job.error_message
        assert job.split_batch_id is None and job.split_mode == SplitMode.OFF.value
        async with test_session_factory() as session:
            messages = [n.message for n in (await session.execute(select(Notification))).scalars()]
        assert any("Datei kann nicht aufgeteilt werden" in m for m in messages)
        assert ocr_mock.calls == []

    async def test_manual_split_above_page_limit_is_rejected(self, fast_settings, test_session_factory, tmp_path):
        doc = await flow.TestManualSplit()._archived(fast_settings, test_session_factory, tmp_path)
        fast_settings.SPLIT_MAX_PAGES = 3
        async with test_session_factory() as session:
            document = await session.get(Document, doc.id)
            with pytest.raises(SplitValidationError, match="Zu viele Seiten"):
                await split_service.request_document_split(session, document, fast_settings)
        # Abgelehnt, bevor eine Kopie entsteht
        assert not fast_settings.split_dir.exists() or not any(fast_settings.split_dir.iterdir())

    async def test_oversized_confirm_body_is_rejected(self, client):
        response = await client.post(
            f"/api/split/{uuid.uuid4()}/confirm",
            json={"version": 0, "parts": [{"pages": list(range(1, 1002))}]},
        )
        assert response.status_code == 422


class TestConcurrentEdits:
    async def test_confirm_and_keep_whole_at_the_same_time(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        """Zwei Geraete: eines bestaetigt, das andere behaelt als ein Dokument.
        Genau eine Aktion gewinnt, die andere bekommt 409 — nie beides."""
        job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))

        async def do_confirm():
            async with test_session_factory() as session:
                return await split_service.confirm(
                    session, batch.id, version=batch.version, parts=[[1], [2, 3]], excluded=[],
                    settings=fast_settings,
                )

        async def do_keep():
            async with test_session_factory() as session:
                return await split_service.keep_whole(
                    session, batch.id, version=batch.version, settings=fast_settings,
                )

        results = await asyncio.gather(do_confirm(), do_keep(), return_exceptions=True)
        conflicts = [r for r in results if isinstance(r, SplitConflictError)]
        assert len(conflicts) == 1, results
        assert not [r for r in results if isinstance(r, Exception) and not isinstance(r, SplitConflictError)]

        final = await _batch(test_session_factory, batch.id)
        job = await _job(test_session_factory, job_id)
        async with test_session_factory() as session:
            parts = await split_service.part_jobs(session, batch.id)
        if final.status == SplitBatchStatus.KEPT_WHOLE:
            assert parts == [] and job.status == JobStatus.PENDING
            assert not list((fast_settings.split_dir / batch.id).glob("teil-*"))
        else:
            assert final.status == SplitBatchStatus.SPLITTING
            assert len(parts) == 2 and job.status == JobStatus.SPLIT

    @pytest.mark.parametrize("action", ["confirm", "keep-whole", "discard"])
    async def test_stale_version_is_a_conflict(
        self, action, client, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 2))
        body = {"version": batch.version + 1}
        if action == "confirm":
            body["parts"] = [{"pages": [1]}, {"pages": [2]}]
        response = await client.post(f"/api/split/{batch.id}/{action}", json=body)
        assert response.status_code == 409
        assert "inzwischen geändert" in response.json()["detail"]
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.PROPOSED
