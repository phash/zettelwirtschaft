from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.audit_log import AuditLog
from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus
from app.models.review_question import ReviewQuestion
from app.models.warranty_info import WarrantyInfo, WarrantyType
from app.services.analysis_service import AnalysisResult
from app.services.archive_service import (
    ArchiveDataError,
    DuplicateDocumentError,
    _match_filing_scope,
    archive_document,
)
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
        # Quelldatei bleibt erhalten (copy+delete-Strategie, Loeschung erst nach Commit)
        assert sample_pdf.exists()
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
        with pytest.raises(DuplicateDocumentError, match="bereits archiviert"):
            await archive_document(
                file_path=file2, original_filename="doc2.pdf",
                stored_filename="abc_doc2.pdf", file_type="pdf",
                file_size_bytes=len(content),
                ocr_result=_make_ocr_result(), analysis_result=_make_analysis_result(),
                settings=test_settings, session=db_session,
            )


async def _archive(test_settings, db_session, file_path, analysis, **kwargs) -> Document:
    return await archive_document(
        file_path=file_path, original_filename=file_path.name,
        stored_filename=f"abc_{file_path.name}", file_type="pdf",
        file_size_bytes=file_path.stat().st_size,
        ocr_result=kwargs.pop("ocr_result", _make_ocr_result()), analysis_result=analysis,
        settings=test_settings, session=db_session, **kwargs,
    )


async def _questions(db_session, doc_id) -> list[ReviewQuestion]:
    result = await db_session.execute(select(ReviewQuestion).where(ReviewQuestion.document_id == doc_id))
    return list(result.scalars().all())


_SCOPES = [
    {"id": "s-privat", "name": "Privat", "slug": "privat", "keywords": [], "is_default": True},
    {"id": "s-praxis", "name": "Praxis", "slug": "praxis", "keywords": ["Praxis"], "is_default": False},
    {"id": "s-firma", "name": "Firma", "slug": "firma", "keywords": ["AG", "  ", ""], "is_default": False},
]


async def _persist_scopes(db_session) -> None:
    """_SCOPES als echte Zeilen (documents.filing_scope_id hat einen FK)."""
    import json

    from app.models.filing_scope import FilingScope

    for scope in _SCOPES:
        db_session.add(FilingScope(
            id=scope["id"], name=scope["name"], slug=scope["slug"],
            keywords=json.dumps(scope["keywords"]), is_default=scope["is_default"],
        ))
    await db_session.flush()


class TestDuplicates:
    async def test_duplicate_error_type_and_message(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path, tmp_path: Path,
    ):
        first = await _archive(test_settings, db_session, sample_pdf, _make_analysis_result(title="Erste"))
        copy = tmp_path / "kopie.pdf"
        copy.write_bytes(sample_pdf.read_bytes())

        with pytest.raises(DuplicateDocumentError) as exc_info:
            await _archive(test_settings, db_session, copy, _make_analysis_result())

        assert exc_info.value.existing_document_id == first.id
        assert str(exc_info.value) == "Diese Datei ist bereits archiviert („Erste“)."

    async def test_deleted_document_allows_reimport(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path, tmp_path: Path,
    ):
        first = await _archive(test_settings, db_session, sample_pdf, _make_analysis_result())
        first.status = DocumentStatus.DELETED
        await db_session.flush()
        copy = tmp_path / "kopie.pdf"
        copy.write_bytes(sample_pdf.read_bytes())

        second = await _archive(test_settings, db_session, copy, _make_analysis_result())
        assert second.id != first.id
        assert second.file_hash == first.file_hash


class TestRobustAnalysisData:
    async def test_null_product_name_and_store(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        warranty = {
            "has_warranty": True, "product_name": None, "store_name": None,
            "purchase_date": "2024-01-15", "warranty_end_date": None,
            "warranty_duration_months": None,
        }
        doc = await _archive(test_settings, db_session, sample_pdf, _make_analysis_result(warranty_info=warranty))
        wi = (await db_session.execute(select(WarrantyInfo).where(WarrantyInfo.document_id == doc.id))).scalar_one()
        assert wi.product_name == "Unbekanntes Produkt"
        assert wi.retailer is None
        assert wi.warranty_duration_months == 24
        assert wi.warranty_end_date == date(2026, 1, 15)  # Kaufdatum + 24 Monate

    async def test_warranty_end_from_duration_and_document_date(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        warranty = {
            "has_warranty": True, "product_name": "Toaster",
            "warranty_duration_months": 36, "warranty_type": "manufacturer",
        }
        doc = await _archive(
            test_settings, db_session, sample_pdf,
            _make_analysis_result(document_date="31.01.2024", warranty_info=warranty),
        )
        wi = (await db_session.execute(select(WarrantyInfo).where(WarrantyInfo.document_id == doc.id))).scalar_one()
        assert wi.purchase_date == date(2024, 1, 31)
        assert wi.warranty_end_date == date(2027, 1, 31)
        assert wi.warranty_type == WarrantyType.MANUFACTURER

    async def test_inconsistent_end_date_recomputed(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        warranty = {
            "has_warranty": True, "product_name": "Lampe",
            "purchase_date": "2024-05-01", "warranty_end_date": "2023-01-01",
        }
        doc = await _archive(test_settings, db_session, sample_pdf, _make_analysis_result(warranty_info=warranty))
        wi = (await db_session.execute(select(WarrantyInfo).where(WarrantyInfo.document_id == doc.id))).scalar_one()
        assert wi.warranty_end_date == date(2026, 5, 1)

    async def test_duplicate_tag_variants(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await _archive(
            test_settings, db_session, sample_pdf,
            _make_analysis_result(tags=["Strom", "strom ", "STROM", "", "Gas"]),
        )
        fresh = await _archive_and_get(db_session, doc.id)
        assert sorted(t.name for t in fresh.tags) == ["gas", "strom"]

    async def test_german_document_date(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await _archive(test_settings, db_session, sample_pdf, _make_analysis_result(document_date="02.06.2025"))
        assert doc.document_date == date(2025, 6, 2)
        assert Path(doc.file_path).parts[-4:-2] == ("2025", "06")

    async def test_unparseable_date_creates_question(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        doc = await _archive(test_settings, db_session, sample_pdf, _make_analysis_result(document_date="Anfang Juni"))
        assert doc.document_date is None
        assert doc.review_status == ReviewStatus.NEEDS_REVIEW
        questions = await _questions(db_session, doc.id)
        date_q = [q for q in questions if q.field_affected == "document_date"]
        assert len(date_q) == 1
        assert "Anfang Juni" in date_q[0].explanation

    async def test_structured_review_questions(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        analysis = _make_analysis_result(
            needs_review=True,
            review_questions=[
                "Freitext-Frage?",
                {
                    "question": "Welcher Dokumenttyp?", "question_type": "classification",
                    "field_affected": "document_type", "suggested_answers": ["RECHNUNG", "QUITTUNG"],
                    "explanation": "LLM nicht erreichbar", "priority": 9,
                },
                {"question": ""},  # leer -> ignoriert
            ],
        )
        doc = await _archive(test_settings, db_session, sample_pdf, analysis)
        questions = {q.question: q for q in await _questions(db_session, doc.id)}
        assert set(questions) == {"Freitext-Frage?", "Welcher Dokumenttyp?"}
        structured = questions["Welcher Dokumenttyp?"]
        assert structured.field_affected == "document_type"
        assert structured.suggested_answers == "RECHNUNG|QUITTUNG"
        assert structured.priority == 9

    async def test_failed_flush_leaves_no_archive_file(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        """Datenfehler beim Speichern: keine verwaiste Kopie im Archiv."""
        from sqlalchemy.exc import IntegrityError

        real_flush = db_session.flush
        calls = {"n": 0}

        async def flaky_flush(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:  # erster Flush (Document) ok, Abschluss-Flush scheitert
                raise IntegrityError("INSERT", {}, Exception("NOT NULL constraint failed"))
            return await real_flush(*args, **kwargs)

        with patch.object(db_session, "flush", side_effect=flaky_flush):
            with pytest.raises(ArchiveDataError):
                await _archive(test_settings, db_session, sample_pdf, _make_analysis_result(tags=[]))

        assert [p for p in Path(test_settings.ARCHIVE_DIR).rglob("*") if p.is_file()] == []

    async def test_created_files_reported(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        created: list[Path] = []
        doc = await _archive(test_settings, db_session, sample_pdf, _make_analysis_result(), created_files=created)
        assert created == [Path(doc.file_path)]


class TestFilingScopeMatching:
    def test_short_keyword_matches_whole_word_only(self):
        analysis = _make_analysis_result()
        match = _match_filing_scope(analysis, _SCOPES, ocr_text="Mietvertrag vom 01.01.")
        assert match.source == "default"  # 'AG' steckt in 'Vertrag' -> kein Treffer
        match = _match_filing_scope(analysis, _SCOPES, ocr_text="Muster AG, Rechnung")
        assert (match.scope_id, match.source) == ("s-firma", "keyword")

    def test_blank_keywords_never_match(self):
        scopes = [
            {"id": "s-privat", "name": "Privat", "slug": "privat", "keywords": [], "is_default": True},
            {"id": "s-leer", "name": "Leer", "slug": "leer", "keywords": ["", "   "], "is_default": False},
        ]
        match = _match_filing_scope(_make_analysis_result(), scopes, ocr_text="beliebiger Text")
        assert match.scope_id == "s-privat"

    def test_long_keyword_matches_compound(self):
        match = _match_filing_scope(_make_analysis_result(), _SCOPES, ocr_text="Zahnarztpraxis Dr. Muster")
        assert match.scope_id == "s-praxis"

    def test_forced_scope_wins_and_falls_back_when_deleted(self):
        analysis = _make_analysis_result()
        match = _match_filing_scope(analysis, _SCOPES, ocr_text="Praxis", forced_scope_id="s-firma")
        assert (match.scope_id, match.source) == ("s-firma", "forced")
        match = _match_filing_scope(analysis, _SCOPES, ocr_text="Praxis", forced_scope_id="geloescht")
        assert (match.scope_id, match.source) == ("s-praxis", "keyword")

    async def test_keyword_match_suppresses_lowconf_scope_question(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        await _persist_scopes(db_session)
        analysis = _make_analysis_result(filing_scope="Privat", filing_scope_confidence=0.5)
        doc = await _archive(
            test_settings, db_session, sample_pdf, analysis,
            ocr_result=_make_ocr_result("Zahnarztpraxis Dr. Muster"), filing_scopes=_SCOPES,
        )
        assert doc.filing_scope_id == "s-praxis"
        assert doc.review_status == ReviewStatus.OK
        assert [q for q in await _questions(db_session, doc.id) if q.field_affected == "filing_scope"] == []

    async def test_lowconf_llm_scope_still_asks_without_keyword(
        self, test_settings: Settings, db_session: AsyncSession, sample_pdf: Path,
    ):
        await _persist_scopes(db_session)
        analysis = _make_analysis_result(filing_scope="Praxis", filing_scope_confidence=0.5)
        doc = await _archive(
            test_settings, db_session, sample_pdf, analysis,
            ocr_result=_make_ocr_result("ohne Treffer"), filing_scopes=_SCOPES,
        )
        questions = [q for q in await _questions(db_session, doc.id) if q.field_affected == "filing_scope"]
        assert len(questions) == 1
        assert questions[0].question == "Zu welchem Ablagebereich gehört dieses Dokument?"
        assert doc.review_status == ReviewStatus.NEEDS_REVIEW
