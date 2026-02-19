from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.audit_log import AuditLog
from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus
from app.models.review_question import ReviewQuestion
from app.models.warranty_info import WarrantyInfo
from app.services.analysis_service import AnalysisResult
from app.services.archive_service import archive_document
from app.services.ocr_service import OcrResult, PageText


def _make_ocr_result(text: str = "Test OCR Text") -> OcrResult:
    return OcrResult(
        full_text=text,
        pages=[PageText(page_number=1, text=text, confidence=0.9)],
        average_confidence=0.9,
        page_count=1,
    )


def _make_analysis_result(**overrides) -> AnalysisResult:
    defaults = dict(
        document_type="RECHNUNG",
        confidence=0.92,
        title="Testrechnung 2024-001",
        sender="Test GmbH",
        recipient="Max Mustermann",
        document_date="2024-03-15",
        amount=119.00,
        currency="EUR",
        reference_number="2024-001",
        tags=["rechnung", "test"],
        summary="Eine Testrechnung",
        tax_relevant=False,
        needs_review=False,
        review_questions=[],
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


async def _archive_and_get(db_session: AsyncSession, doc_id: str) -> Document:
    """Holt Dokument frisch mit allen Relationen."""
    result = await db_session.execute(
        select(Document)
        .where(Document.id == doc_id)
        .options(
            selectinload(Document.tags),
            selectinload(Document.warranty_info),
            selectinload(Document.review_questions),
        )
    )
    return result.scalar_one()


class TestArchiveDocument:
    async def test_archive_creates_document(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await archive_document(
            file_path=sample_pdf, original_filename="rechnung.pdf",
            stored_filename="abc_rechnung.pdf", file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            ocr_result=_make_ocr_result(), analysis_result=_make_analysis_result(),
            settings=test_settings, session=db_session,
        )
        fresh = await _archive_and_get(db_session, doc.id)
        assert fresh.title == "Testrechnung 2024-001"
        assert fresh.document_type == DocumentType.RECHNUNG
        assert fresh.issuer == "Test GmbH"
        assert fresh.status == DocumentStatus.ACTIVE
        assert fresh.review_status == ReviewStatus.OK

    async def test_archive_moves_file(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await archive_document(
            file_path=sample_pdf, original_filename="rechnung.pdf",
            stored_filename="abc_rechnung.pdf", file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            ocr_result=_make_ocr_result(), analysis_result=_make_analysis_result(),
            settings=test_settings, session=db_session,
        )
        assert not sample_pdf.exists()
        assert Path(doc.file_path).exists()
        assert "RECHNUNG" in doc.file_path

    async def test_archive_creates_tags(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await archive_document(
            file_path=sample_pdf, original_filename="rechnung.pdf",
            stored_filename="abc_rechnung.pdf", file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            ocr_result=_make_ocr_result(),
            analysis_result=_make_analysis_result(tags=["elektronik", "haushalt"]),
            settings=test_settings, session=db_session,
        )
        fresh = await _archive_and_get(db_session, doc.id)
        assert len(fresh.tags) == 2
        tag_names = {t.name for t in fresh.tags}
        assert "elektronik" in tag_names
        assert "haushalt" in tag_names

    async def test_archive_creates_warranty_info(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        warranty = {
            "has_warranty": True, "product_name": "Waschmaschine",
            "purchase_date": "2024-01-15", "warranty_end_date": "2026-01-15",
            "warranty_duration_months": 24, "store_name": "MediaMarkt",
        }
        doc = await archive_document(
            file_path=sample_pdf, original_filename="quittung.pdf",
            stored_filename="abc_quittung.pdf", file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            ocr_result=_make_ocr_result(),
            analysis_result=_make_analysis_result(warranty_info=warranty),
            settings=test_settings, session=db_session,
        )
        result = await db_session.execute(
            select(WarrantyInfo).where(WarrantyInfo.document_id == doc.id)
        )
        wi = result.scalar_one()
        assert wi.product_name == "Waschmaschine"
        assert wi.retailer == "MediaMarkt"

    async def test_archive_creates_review_questions(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await archive_document(
            file_path=sample_pdf, original_filename="unklar.pdf",
            stored_filename="abc_unklar.pdf", file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            ocr_result=_make_ocr_result(),
            analysis_result=_make_analysis_result(
                needs_review=True,
                review_questions=["Absender korrekt?", "Betrag stimmt?"],
            ),
            settings=test_settings, session=db_session,
        )
        fresh = await _archive_and_get(db_session, doc.id)
        assert fresh.review_status == ReviewStatus.NEEDS_REVIEW
        result = await db_session.execute(
            select(ReviewQuestion).where(ReviewQuestion.document_id == doc.id)
        )
        assert len(result.scalars().all()) == 2

    async def test_archive_creates_audit_log(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await archive_document(
            file_path=sample_pdf, original_filename="rechnung.pdf",
            stored_filename="abc_rechnung.pdf", file_type="pdf",
            file_size_bytes=sample_pdf.stat().st_size,
            ocr_result=_make_ocr_result(), analysis_result=_make_analysis_result(),
            settings=test_settings, session=db_session,
        )
        result = await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == doc.id)
        )
        assert result.scalar_one().action == "CREATED"

    async def test_duplicate_detection(
        self, test_settings: Settings, db_session: AsyncSession, tmp_path: Path,
    ):
        content = b"%PDF-1.4 test content for hash"
        file1 = tmp_path / "doc1.pdf"
        file1.write_bytes(content)
        file2 = tmp_path / "doc2.pdf"
        file2.write_bytes(content)

        await archive_document(
            file_path=file1, original_filename="doc1.pdf",
            stored_filename="abc_doc1.pdf", file_type="pdf",
            file_size_bytes=len(content),
            ocr_result=_make_ocr_result(), analysis_result=_make_analysis_result(),
            settings=test_settings, session=db_session,
        )
        with pytest.raises(ValueError, match="Duplikat"):
            await archive_document(
                file_path=file2, original_filename="doc2.pdf",
                stored_filename="abc_doc2.pdf", file_type="pdf",
                file_size_bytes=len(content),
                ocr_result=_make_ocr_result(), analysis_result=_make_analysis_result(),
                settings=test_settings, session=db_session,
            )
