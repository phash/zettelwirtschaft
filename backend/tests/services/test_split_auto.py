"""Automatische Pruefung auf mehrere Dokumente (Spezifikation 12, Etappe 2):
Ausloeser im Worker, Ein-Dokument-Pfad ohne zweite OCR, Pruefung bei Schnitt
oder Unsicherheit, Einstellungen, Budgets, Benachrichtigung.

OCR und LLM sind gemockt, die PDFs synthetisch (reportlab), die Seitentexte
erfunden — keine echten Scans, keine Personendaten.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.notification import Notification, NotificationType
from app.models.processing_job import JobSource, JobStatus, ProcessingJob, SplitMode
from app.models.split_batch import SplitBatch, SplitBatchStatus
from app.services import split_service
from app.services.ocr_service import OcrResult, PageText
from tests.services.test_pdf_split_service import make_pdf
from tests.services.test_split_flow import (  # noqa: F401 — Fixtures
    OcrMock,
    _batch,
    _job,
    _pipeline_mocks,
    _run_until,
    _upload,
    fast_settings,
)


def letter(counter: str, *, first: bool) -> str:
    """Erfundene Briefseite; die erste Seite mit Anschrift + Anrede."""
    head = "Beispiel Versand GmbH\nMusterweg 1\n"
    if first:
        head += "Frau Erika Musterfrau\nHauptstraße 5\n12345 Beispielstadt\nSehr geehrte Frau Musterfrau,\n"
    body = "\n".join(f"Inhaltszeile {i} des Schreibens" for i in range(8))
    return f"{head}{body}\n{counter}"


class TextOcr(OcrMock):
    def __init__(self, texts: dict[int, str]):
        super().__init__()
        self.texts = texts

    def ocr(self, image, n, languages, exclude_left=0.0):
        self.calls.append(n)
        return PageText(page_number=n, text=self.texts.get(n, f"Inhalt der Seite {n} mit genug Text"),
                        confidence=0.5 + n / 100)


@pytest.fixture
def text_ocr():
    mock = TextOcr({})
    with patch("app.services.ocr_service.render_pdf_page_for_ocr", side_effect=mock.render), \
            patch("app.services.ocr_service.ocr_page_image", side_effect=mock.ocr):
        yield mock


async def _auto(settings, factory, pdf: Path, **kw) -> str:
    return await _upload(settings, factory, pdf, split_mode=SplitMode.AUTO, **kw)


async def _notifications(factory) -> list[Notification]:
    async with factory() as session:
        return list((await session.execute(select(Notification))).scalars().all())


async def _batch_count(factory) -> int:
    async with factory() as session:
        return (await session.execute(select(func.count(SplitBatch.id)))).scalar_one()


class TestAutoSingleDocument:
    async def test_no_cut_runs_normal_pipeline_with_page_text(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        fast_settings.MAX_OCR_PAGES = 2  # gilt fuer die Erkennung bewusst nicht
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "mehrseiter.pdf", 4))
        await _run_until(test_session_factory, fast_settings, [job_id])

        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.COMPLETED, job.error_message
        assert job.split_batch_id is None and job.split_role is None and job.split_mode == "OFF"
        assert job.page_ocr is None
        _pipeline_mocks["extract"].assert_not_awaited()
        assert sorted(text_ocr.calls) == [1, 2, 3, 4]  # jede Seite genau einmal
        assert await _batch_count(test_session_factory) == 0  # kein Ballast
        async with test_session_factory() as session:
            doc = await session.get(Document, job.document_id)
        assert "Inhalt der Seite 4" in doc.ocr_text  # alle Seiten, nicht nur MAX_OCR_PAGES
        assert doc.ocr_confidence == pytest.approx(sum(0.5 + n / 100 for n in range(1, 5)) / 4)
        assert doc.split_batch_id is None
        # Stapel-Ordner und Vorschaubilder sind aufgeraeumt
        assert list(fast_settings.split_dir.iterdir()) == [] if fast_settings.split_dir.exists() else True
        thumbs = fast_settings.split_thumbnail_dir
        assert not thumbs.exists() or list(thumbs.iterdir()) == []
        assert not [n for n in await _notifications(test_session_factory) if n.type == NotificationType.SPLIT_NEEDED]

    async def test_below_min_pages_is_not_checked(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = None
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "zwei.pdf", 2))
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.split_batch_id is None and job.split_mode == "AUTO"
        assert text_ocr.calls == []
        _pipeline_mocks["extract"].assert_awaited()

    @pytest.mark.parametrize(("auto", "min_pages"), [("false", "3"), ("true", "5")])
    async def test_settings_from_database(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks, auto, min_pages,
    ):
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = None
        async with test_session_factory() as session:
            await split_service.save_split_settings(
                session, split_service.SplitSettings(auto_enabled=auto == "true", min_pages=int(min_pages)),
            )
            await session.commit()
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "vier.pdf", 4))
        await _run_until(test_session_factory, fast_settings, [job_id])
        assert (await _job(test_session_factory, job_id)).split_batch_id is None
        assert text_ocr.calls == []

    async def test_config_default_disables_auto(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        fast_settings.SPLIT_AUTO_ENABLED = False
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = None
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "vier.pdf", 4))
        await _run_until(test_session_factory, fast_settings, [job_id])
        assert text_ocr.calls == []

    async def test_too_many_pages_notifies_and_runs_normally(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        fast_settings.SPLIT_MAX_PAGES = 3
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = None
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "gross.pdf", 4))
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.split_batch_id is None and job.split_mode == "OFF"
        assert text_ocr.calls == []
        titles = [n.title for n in await _notifications(test_session_factory)]
        assert "Zu viele Seiten für die automatische Aufteilung" in titles

    async def test_time_budget_without_findings_is_one_document_with_hint(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        fast_settings.SPLIT_TIME_BUDGET_S = -1  # sofort erschoepft
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = OcrResult(
            full_text="normal erkannt", pages=[PageText(1, "normal erkannt", 0.8)],
            average_confidence=0.8, page_count=3,
        )
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.COMPLETED, job.error_message
        assert text_ocr.calls == []
        _pipeline_mocks["extract"].assert_awaited()  # Seiten ohne Texterkennung -> normale OCR
        messages = [n.message for n in await _notifications(test_session_factory)]
        assert any("Aufteilung nicht vollständig geprüft" in m for m in messages)

    async def test_encrypted_pdf_runs_normally_without_notice(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = None
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "e.pdf", 3, encrypt=True))
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.split_batch_id is None and job.status != JobStatus.AWAITING_SPLIT
        assert not [n for n in await _notifications(test_session_factory) if "aufgeteilt" in n.message]

    async def test_render_error_runs_normally(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        """pdfium kann eine Seite nicht rendern (defekter Inhalt): AUTO darf den
        Job nicht scheitern lassen — Stapel FAILED, Job laeuft normal weiter."""
        import pypdfium2 as pdfium

        _pipeline_mocks["extract"].side_effect = None
        _pipeline_mocks["extract"].return_value = OcrResult(
            full_text="normal erkannt", pages=[PageText(1, "normal erkannt", 0.8)],
            average_confidence=0.8, page_count=3,
        )
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "kaputt.pdf", 3))
        with patch.object(pdfium.PdfPage, "render", side_effect=pdfium.PdfiumError("defekt")):
            await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.COMPLETED, job.error_message
        assert job.retry_count == 0
        assert job.split_mode == "OFF"
        async with test_session_factory() as session:
            batch = await session.get(SplitBatch, job.split_batch_id)
        assert batch.status == SplitBatchStatus.FAILED

    async def test_email_body_is_never_checked(self, fast_settings, test_session_factory, tmp_path, text_ocr):
        import shutil

        dest = Path(fast_settings.UPLOAD_DIR) / "body.pdf"
        shutil.copy2(make_pdf(tmp_path / "s.pdf", 4), dest)
        async with test_session_factory() as session:
            body = ProcessingJob(
                original_filename="mail.pdf", stored_filename="body.pdf", file_path=str(dest),
                file_type="pdf", file_size_bytes=1, source=JobSource.EMAIL, status=JobStatus.PENDING,
                ocr_text="Text der E-Mail",
            )
            session.add(body)
            await session.commit()
        await _run_until(test_session_factory, fast_settings, [body.id])
        assert (await _job(test_session_factory, body.id)).status == JobStatus.COMPLETED
        assert text_ocr.calls == []


class TestAutoMultipleDocuments:
    @pytest.mark.parametrize("source", [JobSource.UPLOAD, JobSource.WATCH_FOLDER, JobSource.EMAIL])
    async def test_two_letters_wait_for_review(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, source,
    ):
        text_ocr.texts.update({
            1: letter("Seite 1 von 2", first=True), 2: letter("Seite 2 von 2", first=False),
            3: letter("Seite 1 von 2", first=True), 4: letter("Seite 2 von 2", first=False),
        })
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "scan_0427.pdf", 4),
                             source=source)
        await _run_until(test_session_factory, fast_settings, [job_id])

        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.AWAITING_SPLIT, job.error_message
        batch = await _batch(test_session_factory, job.split_batch_id)
        assert batch.status == SplitBatchStatus.PROPOSED
        assert batch.job_source == source.value
        assert batch.detector_version == "h1" and batch.llm_used is False
        proposal = json.loads(batch.proposal)
        assert [b["after_page"] for b in proposal["boundaries"] if b["decision"] == "cut"] == [2]
        assert "Seitenzähler beginnt neu: Seite 1" in proposal["boundaries"][1]["reasons"]
        pages = json.loads(batch.pages)
        assert pages[0]["sig"]["counter"] == [1, 2] and pages[0]["sig"]["addr"] is True
        assert "Musterfrau" not in batch.pages  # kein Klartext am Stapel
        assert sorted(text_ocr.calls) == [1, 2, 3, 4]

        notes = [n for n in await _notifications(test_session_factory) if n.type == NotificationType.SPLIT_NEEDED]
        assert len(notes) == 1 and notes[0].document_id is None
        assert notes[0].message == "„scan_0427.pdf“ enthält vermutlich 2 Dokumente – bitte prüfen."

    async def test_uncertain_transition_goes_to_review(
        self, fast_settings, test_session_factory, tmp_path, text_ocr,
    ):
        # Neue Anschrift ohne Seitenzaehler: weder sicher Schnitt noch sicher keiner
        text_ocr.texts.update({3: "Musterweg 1\n12345 Beispielstadt\n" + "\n".join(["Text"] * 6)})
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        await _run_until(test_session_factory, fast_settings, [job_id])
        job = await _job(test_session_factory, job_id)
        assert job.status == JobStatus.AWAITING_SPLIT
        proposal = json.loads((await _batch(test_session_factory, job.split_batch_id)).proposal)
        assert [b["decision"] for b in proposal["boundaries"]] == ["none", "uncertain"]
        notes = [n for n in await _notifications(test_session_factory) if n.type == NotificationType.SPLIT_NEEDED]
        assert notes[0].message == "„s.pdf“ enthält möglicherweise mehrere Dokumente – bitte prüfen."

    async def test_confirmed_parts_reuse_page_text(
        self, fast_settings, test_session_factory, tmp_path, text_ocr, _pipeline_mocks,
    ):
        text_ocr.texts.update({
            1: letter("Seite 1 von 1", first=True), 2: letter("Seite 1 von 2", first=True),
            3: letter("Seite 2 von 2", first=False),
        })
        job_id = await _auto(fast_settings, test_session_factory, make_pdf(tmp_path / "s.pdf", 3))
        await _run_until(test_session_factory, fast_settings, [job_id])
        batch = await _batch(test_session_factory, (await _job(test_session_factory, job_id)).split_batch_id)
        async with test_session_factory() as session:
            job_ids = await split_service.confirm(
                session, batch.id, version=batch.version, parts=[[1], [2, 3]], excluded=[],
                settings=fast_settings,
            )
        await _run_until(test_session_factory, fast_settings, job_ids)
        _pipeline_mocks["extract"].assert_not_awaited()
        assert sorted(text_ocr.calls) == [1, 2, 3]  # keine Seite zweimal erkannt
        assert (await _batch(test_session_factory, batch.id)).status == SplitBatchStatus.DONE

    async def test_force_notifies_even_without_cut(self, fast_settings, test_session_factory, tmp_path, text_ocr):
        job_id = await _upload(fast_settings, test_session_factory, make_pdf(tmp_path / "f.pdf", 2))
        await _run_until(test_session_factory, fast_settings, [job_id])
        assert (await _job(test_session_factory, job_id)).status == JobStatus.AWAITING_SPLIT
        notes = [n for n in await _notifications(test_session_factory) if n.type == NotificationType.SPLIT_NEEDED]
        assert notes[0].message == "Die Seiten von „f.pdf“ sind erkannt – bitte aufteilen."


class TestSplitSettings:
    async def test_defaults_and_invalid_values(self, test_settings, test_session_factory):
        async with test_session_factory() as session:
            value = await split_service.load_split_settings(session, test_settings)
            assert value == split_service.SplitSettings(auto_enabled=True, min_pages=3)
            from app.services.settings_service import set_db_setting

            await set_db_setting(session, split_service.SETTING_MIN_PAGES, "kaputt")
            await session.commit()
            assert (await split_service.load_split_settings(session, test_settings)).min_pages == 3
