"""Aufteilen von Stapel-PDFs (Spezifikation 12, Etappe 1): Worker-Stufe,
Bestaetigen/Behalten/Verwerfen, Teil-Pipeline, Duplikate, Recovery, MANUAL.

OCR (Rendern + Tesseract) und das LLM sind gemockt; die PDFs sind
synthetisch (reportlab) — keine echten Scans, keine Personendaten.
"""

import json
import uuid
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import Settings
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentTag, Tag
from app.models.filing_scope import FilingScope
from app.models.notification import Notification
from app.models.processing_job import (
    JOB_ERROR_DUPLICATE,
    JobSource,
    JobStatus,
    ProcessingJob,
    SplitMode,
    SplitRole,
)
from app.models.split_batch import SplitBatch, SplitBatchStatus, SplitOrigin
from app.services import split_service
from app.services.analysis_service import AnalysisResult
from app.services.ocr_service import PageText
from app.services.queue_worker_service import (
    purge_duplicate_upload_copies,
    recover_interrupted_jobs,
)
from app.services.split_service import SplitConflictError, SplitValidationError, validate_parts
from app.services.upload_service import create_job
from tests.services.test_pdf_split_service import make_pdf
from tests.services.test_queue_worker_service import _job_reaches, _run_worker_until


@pytest.fixture
def fast_settings(test_settings: Settings) -> Settings:
    test_settings.QUEUE_POLL_INTERVAL = 0
    return test_settings


class OcrMock:
    """Zaehlt OCR-Aufrufe pro Seite; ``fail_on`` wirft einmalig bei Seite n."""

    def __init__(self):
        self.calls: list[int] = []
        self.fail_on: int | None = None

    def render(self, path, n):
        return Image.new("L", (1240, 1754), 255)

    def ocr(self, image, n, languages, exclude_left=0.0):
        self.calls.append(n)
        if self.fail_on == n:
            self.fail_on = None
            raise RuntimeError("Tesseract abgestuerzt")
        return PageText(page_number=n, text=f"Inhalt der Seite {n} mit genug Text", confidence=0.5 + n / 100)


@pytest.fixture
def ocr_mock():
    mock = OcrMock()
    with patch("app.services.ocr_service.render_pdf_page_for_ocr", side_effect=mock.render), \
            patch("app.services.ocr_service.ocr_page_image", side_effect=mock.ocr):
        yield mock


@pytest.fixture(autouse=True)
def _pipeline_mocks():
    """LLM, Thumbnail und Vektorisierung mocken; die normale OCR darf fuer
    aufgeteilte Teile NIE laufen (Seitentext wird wiederverwendet)."""
    analysis = AnalysisResult(document_type="RECHNUNG", confidence=0.9, title="Teil", needs_review=False)
    with patch(
        "app.services.analysis_service._try_combined_analysis",
        new_callable=AsyncMock, return_value=analysis,
    ) as llm, patch(
        "app.services.analysis_service.extract_text",
        new_callable=AsyncMock, side_effect=AssertionError("normale OCR darf nicht laufen"),
    ) as extract, patch(
        "app.services.queue_worker_service.generate_thumbnail", new_callable=AsyncMock, return_value=None,
    ), patch(
        "app.services.vectorize_service.vectorize_document", new_callable=AsyncMock, return_value=0,
    ), patch(
        "app.services.vectorize_service.delete_document_vectors", new_callable=AsyncMock,
    ), patch(
        # KI-Pass der Aufteilung: Ollama "nicht erreichbar" (Tests erreichen nie
        # einen echten Dienst); Tests zum KI-Pass setzen return_value selbst.
        "app.services.split_llm_service.call_llm", new_callable=AsyncMock, return_value=None,
    ) as split_llm:
        yield {"llm": llm, "extract": extract, "split_llm": split_llm}


async def _upload(settings: Settings, factory, pdf: Path, *, split_mode=SplitMode.FORCE, **kw) -> str:
    dest = Path(settings.UPLOAD_DIR) / f"{uuid.uuid4().hex[:8]}_{pdf.name}"
    shutil.copy2(pdf, dest)
    async with factory() as session:
        job = await create_job(
            dest, dest.name, pdf.name, dest.stat().st_size, kw.pop("source", JobSource.UPLOAD), session,
            split_mode=split_mode, **kw,
        )
        await session.commit()
        return job.id


async def _job(factory, job_id) -> ProcessingJob | None:
    async with factory() as session:
        return await session.get(ProcessingJob, job_id)


async def _batch(factory, batch_id) -> SplitBatch:
    async with factory() as session:
        return await session.get(SplitBatch, batch_id)


async def _run_until(factory, settings, job_ids, statuses=frozenset(
    {JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED, JobStatus.AWAITING_SPLIT}
), *, files_gone: list[Path] = ()):
    """Ein Worker-Lauf, bis ALLE Jobs einen der Zustaende erreicht haben (ein
    Abbruch mitten in einem zweiten Job liesse ihn sonst PROCESSING stehen)."""
    conditions = [_job_reaches(factory, job_id, statuses) for job_id in job_ids]

    async def all_reached() -> bool:
        # files_gone: der Worker loescht Job-Dateien erst nach dem Commit
        return all([await c() for c in conditions]) and not any(p.exists() for p in files_gone)

    await _run_worker_until(factory, settings, all_reached)


async def _detect(settings, factory, pdf: Path, **kw) -> tuple[str, SplitBatch]:
    job_id = await _upload(settings, factory, pdf, **kw)
    await _run_until(factory, settings, [job_id])
    job = await _job(factory, job_id)
    assert job.status == JobStatus.AWAITING_SPLIT, job.error_message
    return job_id, await _batch(factory, job.split_batch_id)


async def _confirm(factory, settings, batch: SplitBatch, parts, excluded=()):
    async with factory() as session:
        return await split_service.confirm(
            session, batch.id, version=batch.version, parts=parts, excluded=list(excluded), settings=settings,
        )


class TestDetectionStage:
    async def test_force_upload_waits_in_editor_with_page_text(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        pdf = make_pdf(tmp_path / "stapel.pdf", 5, blank={3})
        job_id, batch = await _detect(fast_settings, test_session_factory, pdf)

        assert batch.status == SplitBatchStatus.PROPOSED
        assert batch.origin == SplitOrigin.AUTO
        assert batch.page_count == batch.pages_done == 5
        assert batch.detector_version == "h1"
        pages = json.loads(batch.pages)
        assert [p["blank"] for p in pages] == [False, False, True, False, False]
        assert [p["source"] for p in pages] == ["ocr", "ocr", "none", "ocr", "ocr"]
        assert all("text" not in p for p in pages)  # kein Volltext am Stapel
        proposal = json.loads(batch.proposal)
        # Synthetische Seiten ohne Signale: FORCE wartet trotzdem im Editor
        assert [b["decision"] for b in proposal["boundaries"]] == ["none"] * 4
        assert all("sig" in p for p in pages)
        # Leerseite ohne OCR, jede andere Seite genau einmal
        assert sorted(ocr_mock.calls) == [1, 2, 4, 5]

        job = await _job(test_session_factory, job_id)
        assert job.split_role == SplitRole.SOURCE.value
        assert job.document_id is None
        assert Path(job.file_path) == fast_settings.split_dir / batch.id / "source.pdf"
        assert Path(job.file_path).is_file()
        assert list(Path(fast_settings.UPLOAD_DIR).iterdir()) == []  # Upload-Kopie entfernt
        assert len(json.loads(job.page_ocr)) == 5
        thumbs = fast_settings.split_thumbnail_dir / batch.id
        assert sorted(p.name for p in thumbs.iterdir()) == [
            *(f"p{n:03d}.png" for n in range(1, 6)), *(f"v{n:03d}.jpg" for n in range(1, 6)),
        ]
        with Image.open(thumbs / "p001.png") as img:
            assert img.size[0] == fast_settings.SPLIT_THUMBNAIL_WIDTH

    async def test_confirm_runs_parts_through_pipeline_without_second_ocr(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        pdf = make_pdf(tmp_path / "stapel.pdf", 5, blank={3})
        source_job_id, batch = await _detect(fast_settings, test_session_factory, pdf)

        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1, 2], [4, 5]], excluded=[3])
        assert len(job_ids) == 2
        assert (await _job(test_session_factory, source_job_id)).status == JobStatus.SPLIT
        part_files = [split_service.part_path(fast_settings, batch.id, i) for i in (1, 2)]
        assert all(p.is_file() for p in part_files)
        await _run_until(test_session_factory, fast_settings, job_ids, files_gone=part_files)

        _pipeline_mocks["extract"].assert_not_awaited()
        assert sorted(ocr_mock.calls) == [1, 2, 4, 5]  # keine Seite zweimal erkannt
        async with test_session_factory() as session:
            docs = (await session.execute(
                select(Document).where(Document.split_batch_id == batch.id).order_by(Document.split_part_index)
            )).scalars().all()
            audits = (await session.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.SPLIT_CREATED)
            )).scalars().all()
            parts = [await session.get(ProcessingJob, i) for i in job_ids]
        assert [d.source_pages for d in docs] == ["1-2", "4-5"]
        assert [d.split_part_index for d in docs] == [1, 2]
        assert docs[0].original_filename == "stapel (Teil 1 von 2).pdf"
        assert "Inhalt der Seite 1" in docs[0].ocr_text and "Inhalt der Seite 2" in docs[0].ocr_text
        assert docs[0].ocr_confidence == pytest.approx((0.51 + 0.52) / 2)  # Seitenmittel
        assert "Teil 2/2, Seiten 4–5 aus stapel.pdf" in audits[1].details or \
            "Teil 2/2, Seiten 4–5 aus stapel.pdf" in audits[0].details
        assert all(p.page_ocr is None and p.split_mode == "OFF" for p in parts)
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE

    async def test_double_confirm_creates_one_split(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3]])
        with pytest.raises(SplitConflictError):
            await _confirm(test_session_factory, fast_settings, batch, [[1], [2, 3]])
        async with test_session_factory() as session:
            assert len(await split_service.part_jobs(session, batch.id)) == 2
            # idempotent: erneutes Anlegen (Recovery) erzeugt keine Duplikate
            await split_service.create_part_jobs(session, batch.id, fast_settings)
            assert len(await split_service.part_jobs(session, batch.id)) == 2

    async def test_keep_whole_reuses_page_text(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        async with test_session_factory() as session:
            await split_service.keep_whole(session, batch.id, version=batch.version, settings=fast_settings)
        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.PENDING and job.split_mode == "OFF"

        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.COMPLETED, job.error_message
        _pipeline_mocks["extract"].assert_not_awaited()
        assert sorted(ocr_mock.calls) == [1, 2, 3]
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.KEPT_WHOLE
        async with test_session_factory() as session:
            doc = await session.get(Document, job.document_id)
        assert doc.split_batch_id is None and "Inhalt der Seite 3" in doc.ocr_text

    async def test_discard_removes_job_and_files_and_allows_reimport(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        pdf = make_pdf(tmp_path / "s.pdf", 2)
        job_id, batch = await _detect(fast_settings, test_session_factory, pdf)
        async with test_session_factory() as session:
            await split_service.discard(session, batch.id, version=batch.version, settings=fast_settings)
        assert await _job(test_session_factory, job_id) is None
        assert not (fast_settings.split_dir / batch.id).exists()
        assert not (fast_settings.split_thumbnail_dir / batch.id).exists()
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DISCARDED

        _, again = await _detect(fast_settings, test_session_factory, pdf)  # kein Duplikat
        assert again.id != batch.id

    async def test_reimport_of_split_stack_is_duplicate(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        pdf = make_pdf(tmp_path / "s.pdf", 2)
        _, batch = await _detect(fast_settings, test_session_factory, pdf)
        await _confirm(test_session_factory, fast_settings, batch, [[1], [2]])

        for mode in (SplitMode.FORCE, SplitMode.AUTO):
            job_id = await _upload(fast_settings, test_session_factory, pdf, split_mode=mode)
            await _run_until(test_session_factory, fast_settings, [job_id])
            job = await _job(test_session_factory, job_id)
            assert job.status == JobStatus.FAILED
            assert job.error_code == JOB_ERROR_DUPLICATE
            assert "bereits aufgeteilt" in job.error_message

    async def test_part_duplicate_counts_as_done(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 2))
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1], [2]])
        # Teil 1 ist schon (z.B. frueher einzeln gescannt) aktiv archiviert
        from app.services.archive_service import _compute_file_hash
        from tests.factories import make_document

        async with test_session_factory() as session:
            first = await session.get(ProcessingJob, job_ids[0])
            await make_document(session, commit=True, file_hash=_compute_file_hash(Path(first.file_path)))

        await _run_until(test_session_factory, fast_settings, job_ids)
        statuses = [(await _job(test_session_factory, i)).error_code for i in job_ids]
        assert statuses == [JOB_ERROR_DUPLICATE, None]
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE

    async def test_checkpoint_resume_after_crash(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        ocr_mock.fail_on = 7
        job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 8))
        job = await _job(test_session_factory, job_id)
        assert job.retry_count == 1  # der Absturz verbrauchte einen Versuch
        # Seiten 1-5 lagen im Checkpoint und wurden nicht erneut erkannt
        assert all(ocr_mock.calls.count(n) == 1 for n in range(1, 6))
        assert batch.pages_done == 8 and len(json.loads(job.page_ocr)) == 8

    async def test_email_body_and_part_jobs_are_never_detected(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        pdf = make_pdf(tmp_path / "s.pdf", 3)
        dest = Path(fast_settings.UPLOAD_DIR) / "body.pdf"
        shutil.copy2(pdf, dest)
        async with test_session_factory() as session:
            body = ProcessingJob(
                original_filename="mail.pdf", stored_filename="body.pdf", file_path=str(dest),
                file_type="pdf", file_size_bytes=1, source=JobSource.EMAIL, status=JobStatus.PENDING,
                split_mode=SplitMode.FORCE.value, ocr_text="Text der E-Mail",
            )
            session.add(body)
            await session.commit()
        await _run_until(test_session_factory, fast_settings, [body.id])
        assert (await _job(test_session_factory, body.id)).status == JobStatus.COMPLETED
        assert ocr_mock.calls == []

    async def test_too_many_pages_runs_normal_pipeline(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        fast_settings.SPLIT_MAX_PAGES = 2
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = None
        job_id = await _upload(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.NEEDS_REVIEW  # OCR-Mock liefert keinen Text
        assert job.split_batch_id is None and job.split_mode == "OFF"
        async with test_session_factory() as session:
            notes = (await session.execute(select(Notification))).scalars().all()
        assert any("zu viele für die Aufteilung" in n.message for n in notes)

    async def test_one_page_pdf_is_not_split(self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks):
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = None
        job_id = await _upload(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 1))
        await _run_until(test_session_factory, fast_settings, [job_id])
        assert (await _job(test_session_factory, job_id)).split_batch_id is None


class TestValidation:
    PAGES = [{"n": n, "blank": n == 3} for n in range(1, 6)]

    def test_valid_split_with_exclusion(self):
        specs = validate_parts([[1, 2], [4, 5]], [3], self.PAGES, 5, 50)
        assert [s.pages for s in specs] == [(1, 2), (4, 5)]

    def test_exclusion_inside_a_part_is_allowed(self):
        assert validate_parts([[1, 2, 4, 5]], [3], self.PAGES, 5, 50)[0].pages == (1, 2, 4, 5)

    @pytest.mark.parametrize("parts,excluded,message", [
        ([[1, 2], [4, 5]], [], "Seite 3 ist keinem Teil"),
        ([[1, 2, 3], [3, 4, 5]], [], "mehreren Teilen"),
        ([[2, 1], [3, 4, 5]], [], "nicht aufsteigend"),
        ([[1, 2, 5], [3, 4]], [], "nicht zusammenhängend"),
        ([[1, 2], [3], [4, 5]], [], "nur aus Leerseiten"),
        ([[1, 2, 3, 4, 5, 6]], [], "Seite 6 gibt es"),
        ([[3, 4, 5], [1, 2]], [], "Seitenreihenfolge"),
        ([[]], [], "Mindestens eine Seite"),
        ([[1, 2], [], [3, 4, 5]], [], "keine Seite"),
    ])
    def test_invalid_splits(self, parts, excluded, message):
        with pytest.raises(SplitValidationError, match=message):
            validate_parts(parts, excluded, self.PAGES, 5, 50)

    def test_max_parts(self):
        with pytest.raises(SplitValidationError, match="Höchstens 2 Teile"):
            validate_parts([[1], [2], [3, 4, 5]], [], [], 5, 2)

    def test_page_ranges(self):
        assert split_service.format_page_ranges([3, 4, 5, 7, 9, 10]) == "3-5,7,9-10"
        assert split_service.format_page_ranges(list(range(1, 100, 2))) == "1-99"  # > 40 Zeichen


class TestStatesAndRecovery:
    async def test_recover_interrupted_jobs_leaves_awaiting_split(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        job_id, _ = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 2))
        async with test_session_factory() as session:
            assert await recover_interrupted_jobs(session, fast_settings) == 0
        assert (await _job(test_session_factory, job_id)).status == JobStatus.AWAITING_SPLIT

    async def test_recovery_of_detecting_and_splitting_batches(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        _, before = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "a.pdf", 3))
        _, after = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "b.pdf", 3))
        final = {"parts": [{"index": 1, "pages": [1]}, {"index": 2, "pages": [2, 3]}], "excluded": []}
        async with test_session_factory() as session:
            for batch, written in ((before, False), (after, True)):
                row = await session.get(SplitBatch, batch.id)
                row.status = SplitBatchStatus.SPLITTING
                row.final_parts = json.dumps({**final, "parts_written": written})
            orphan = SplitBatch(
                origin=SplitOrigin.AUTO, status=SplitBatchStatus.DETECTING, source_job_id="weg",
                job_source="UPLOAD", original_filename="x.pdf", file_path="/x", file_hash="h", page_count=2,
            )
            session.add(orphan)
            await session.commit()
        for batch in (before, after):
            for idx, pages in ((1, [1]), (2, [2, 3])):
                from app.services import pdf_split_service

                pdf_split_service.write_part(
                    Path(batch.file_path), pages, split_service.part_path(fast_settings, batch.id, idx),
                )
        leftover = split_service.part_path(fast_settings, before.id, 3).with_suffix(".pdf.tmp")
        leftover.write_bytes(b"halb")

        async with test_session_factory() as session:
            assert await split_service.recover_interrupted_batches(session, fast_settings) == 3
            # Zweiter Start: nur noch der SPLITTING-Stapel (Teile laufen), keine neuen Jobs
            assert await split_service.recover_interrupted_batches(session, fast_settings) == 1
            assert len(await split_service.part_jobs(session, after.id)) == 2
            assert await split_service.part_jobs(session, before.id) == []

        assert (await _batch(test_session_factory, before.id)).status == SplitBatchStatus.PROPOSED
        assert not any((fast_settings.split_dir / before.id).glob("teil-*"))
        assert (await _batch(test_session_factory, after.id)).status == SplitBatchStatus.SPLITTING
        assert (await _batch(test_session_factory, orphan.id)).status == SplitBatchStatus.FAILED

    async def test_source_missing_on_confirm(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        job_id, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 2))
        Path(batch.file_path).unlink()
        with pytest.raises(split_service.SplitSourceMissingError):
            await _confirm(test_session_factory, fast_settings, batch, [[1], [2]])
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.PROPOSED
        assert (await _job(test_session_factory, job_id)).error_code == "SPLIT_SOURCE_MISSING"

    async def test_purge_skips_sources_of_active_batches(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 2))
        async with test_session_factory() as session:
            session.add(ProcessingJob(
                original_filename="dup.pdf", stored_filename="dup.pdf", file_path=batch.file_path,
                file_type="pdf", file_size_bytes=1, source=JobSource.UPLOAD, status=JobStatus.FAILED,
                error_code=JOB_ERROR_DUPLICATE,
            ))
            await session.commit()
        assert await purge_duplicate_upload_copies(test_session_factory, fast_settings) == 0
        assert Path(batch.file_path).is_file()


class TestManualSplit:
    async def _archived(self, settings, factory, tmp_path) -> Document:
        archive = Path(settings.ARCHIVE_DIR) / "privat" / "2026" / "01" / "SONSTIGES"
        archive.mkdir(parents=True)
        path = make_pdf(archive / "abc_sammel.pdf", 4)
        from app.services.archive_service import _compute_file_hash
        from tests.factories import make_document

        async with factory() as session:
            scope = FilingScope(name="Praxis", slug="praxis", keywords="[]")
            session.add(scope)
            await session.flush()
            doc = await make_document(
                session, original_filename="sammel.pdf", file_path=str(path),
                file_hash=_compute_file_hash(path), filing_scope_id=scope.id,
            )
            manual, auto, added = Tag(name="projekt-x", is_auto_generated=False), Tag(name="rechnung"), Tag(name="strom")
            session.add_all([manual, auto, added])
            await session.flush()
            for tag in (manual, auto, added):
                session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
            session.add(AuditLog(document_id=doc.id, action=AuditAction.TAG_ADDED, details='{"tag": "strom"}'))
            await session.commit()
            return doc

    async def test_manual_split_inherits_scope_and_manual_tags(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        doc = await self._archived(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            batch, job = await split_service.request_document_split(session, doc, fast_settings)
            with pytest.raises(SplitConflictError, match="läuft bereits"):
                await split_service.request_document_split(session, doc, fast_settings)
        await _run_until(test_session_factory, fast_settings, [job.id])
        assert (await _job(test_session_factory, job.id)).status == JobStatus.AWAITING_SPLIT
        batch = await _batch(test_session_factory, batch.id)
        assert batch.origin == SplitOrigin.MANUAL and batch.source_document_id == doc.id

        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1, 2], [3, 4]])
        # Erst nach ALLEN Teilen: Teil 2 vorerst zurueckhalten (nicht PENDING,
        # nicht terminal)
        async with test_session_factory() as session:
            held = await session.get(ProcessingJob, job_ids[1])
            held.status = JobStatus.PROCESSING
            await session.commit()
        await _run_until(test_session_factory, fast_settings, job_ids[:1])
        async with test_session_factory() as session:
            original = await session.get(Document, doc.id)
            assert original.status == DocumentStatus.ACTIVE
            held = await session.get(ProcessingJob, job_ids[1])
            held.status = JobStatus.PENDING
            await session.commit()
        await _run_until(test_session_factory, fast_settings, job_ids)

        async with test_session_factory() as session:
            original = await session.get(Document, doc.id)
            parts = (await session.execute(
                select(Document).where(Document.split_batch_id == batch.id)
            )).scalars().all()
            split_audit = (await session.execute(
                select(AuditLog).where(AuditLog.document_id == doc.id, AuditLog.action == AuditAction.SPLIT)
            )).scalar_one()
            tag_names = {
                part.id: sorted(t.name for t in (await session.execute(
                    select(Tag).join(DocumentTag).where(DocumentTag.document_id == part.id)
                )).scalars())
                for part in parts
            }
        assert original.status == DocumentStatus.DELETED
        assert Path(original.file_path).is_file()  # Archivdatei bleibt (Soft-Delete)
        assert sorted(json.loads(split_audit.details)["parts"]) == sorted(p.id for p in parts)
        assert all(p.filing_scope_id == doc.filing_scope_id for p in parts)
        assert all({"projekt-x", "strom"} <= set(names) and "rechnung" not in names for names in tag_names.values())
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE

    async def test_missing_original_is_tolerated(self, fast_settings, test_session_factory, tmp_path, ocr_mock):
        doc = await self._archived(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            batch, job = await split_service.request_document_split(session, doc, fast_settings)
        await _run_until(test_session_factory, fast_settings, [job.id])
        batch = await _batch(test_session_factory, batch.id)
        async with test_session_factory() as session:
            row = await session.get(SplitBatch, batch.id)
            row.source_document_id = "00000000-0000-4000-8000-000000000000"  # Original fehlt
            await session.commit()
        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1, 2, 3, 4]])
        await _run_until(test_session_factory, fast_settings, job_ids)
        assert (await _job(test_session_factory, job_ids[0])).status == JobStatus.COMPLETED
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE

    async def test_request_validation(self, fast_settings, test_session_factory, tmp_path):
        doc = await self._archived(fast_settings, test_session_factory, tmp_path)
        async with test_session_factory() as session:
            one_page = await session.get(Document, doc.id)
            make_pdf(Path(one_page.file_path), 1)
            with pytest.raises(SplitValidationError, match="nur eine Seite"):
                await split_service.request_document_split(session, one_page, fast_settings)
            one_page.file_type = "jpg"
            with pytest.raises(SplitValidationError, match="Nur PDF"):
                await split_service.request_document_split(session, one_page, fast_settings)
            one_page.file_type = "pdf"
            one_page.status = DocumentStatus.DELETED
            with pytest.raises(SplitValidationError, match="Gelöschte"):
                await split_service.request_document_split(session, one_page, fast_settings)


class TestBudget:
    async def test_time_budget_marks_partial_and_parts_use_normal_ocr(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock, _pipeline_mocks,
    ):
        fast_settings.SPLIT_TIME_BUDGET_S = -1  # sofort erschoepft
        _, batch = await _detect(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        assert json.loads(batch.proposal)["partial_from"] == 1
        assert {p["source"] for p in json.loads(batch.pages)} == {"skipped"}
        assert ocr_mock.calls == []

        job_ids = await _confirm(test_session_factory, fast_settings, batch, [[1, 2, 3]])
        part = await _job(test_session_factory, job_ids[0])
        assert part.page_ocr is None  # Teil wird spaeter normal erkannt

    async def test_pause_takes_effect_at_next_checkpoint(
        self, fast_settings, test_session_factory, tmp_path, ocr_mock,
    ):
        from app.services import split_detection_service

        job_id = await _upload(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 8))
        async with test_session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            job.status = JobStatus.PROCESSING
            await session.commit()
            decision = await split_detection_service.maybe_detect(
                job, fast_settings, session, should_pause=lambda: True,
            )
        assert decision.kind == "PAUSED"
        job = await _job(test_session_factory, job_id)
        batch = await _batch(test_session_factory, job.split_batch_id)
        assert job.status == JobStatus.PENDING
        assert batch.status == SplitBatchStatus.DETECTING and batch.pages_done == 5
        assert sorted(ocr_mock.calls) == [1, 2, 3, 4, 5]
