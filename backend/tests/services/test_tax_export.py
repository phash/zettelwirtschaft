"""Tests fuer Steuerpaket-Export Service."""

import uuid
from datetime import date

import pytest

from app.models.document import Document, DocumentType, TaxCategory
from app.services.tax_export_service import get_tax_summary, validate_export


async def _create_tax_doc(session, year: int, amount: float, category: TaxCategory) -> Document:
    """Hilfsfunktion: Erstellt ein steuerrelevantes Dokument."""
    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        original_filename="beleg.pdf",
        stored_filename=f"{doc_id}_beleg.pdf",
        file_path=f"./data/archive/{doc_id}_beleg.pdf",
        file_type="pdf",
        file_size_bytes=1000,
        file_hash=str(uuid.uuid4()),
        document_type=DocumentType.RECHNUNG,
        title="Testbeleg",
        ocr_text="test",
        tax_relevant=True,
        tax_year=year,
        tax_category=category,
        amount=amount,
        document_date=date(year, 6, 15),
    )
    session.add(doc)
    await session.commit()
    return doc


@pytest.mark.asyncio
class TestTaxSummary:
    async def test_empty_year(self, db_session):
        """Keine Dokumente fuer ein Jahr."""
        result = await get_tax_summary(db_session, 2025)
        assert result["total_documents"] == 0
        assert result["categories"] == []

    async def test_with_documents(self, db_session):
        """Zusammenfassung mit Dokumenten."""
        await _create_tax_doc(db_session, 2025, 100.0, TaxCategory.WERBUNGSKOSTEN)
        await _create_tax_doc(db_session, 2025, 250.0, TaxCategory.WERBUNGSKOSTEN)
        await _create_tax_doc(db_session, 2025, 50.0, TaxCategory.HANDWERKERLEISTUNGEN)

        result = await get_tax_summary(db_session, 2025)
        assert result["total_documents"] == 3
        assert result["total_amount"] == 400.0
        assert len(result["categories"]) == 2

    async def test_wrong_year_excluded(self, db_session):
        """Dokumente aus anderem Jahr nicht mitgezaehlt."""
        await _create_tax_doc(db_session, 2024, 100.0, TaxCategory.WERBUNGSKOSTEN)
        result = await get_tax_summary(db_session, 2025)
        assert result["total_documents"] == 0


@pytest.mark.asyncio
class TestTaxValidation:
    async def test_not_ready_when_empty(self, db_session):
        """Nicht bereit ohne Dokumente."""
        result = await validate_export(db_session, 2025)
        assert result["ready"] is False

    async def test_ready_with_documents(self, db_session):
        """Bereit mit Dokumenten."""
        await _create_tax_doc(db_session, 2025, 100.0, TaxCategory.WERBUNGSKOSTEN)
        result = await validate_export(db_session, 2025)
        assert result["ready"] is True

    async def test_warnings_for_missing_amount(self, db_session):
        """Warnung bei fehlendem Betrag."""
        doc_id = str(uuid.uuid4())
        doc = Document(
            id=doc_id,
            original_filename="beleg.pdf",
            stored_filename=f"{doc_id}_beleg.pdf",
            file_path=f"./data/archive/{doc_id}_beleg.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            file_hash=str(uuid.uuid4()),
            document_type=DocumentType.RECHNUNG,
            title="Beleg ohne Betrag",
            ocr_text="test",
            tax_relevant=True,
            tax_year=2025,
            tax_category=TaxCategory.WERBUNGSKOSTEN,
            amount=None,
        )
        db_session.add(doc)
        await db_session.commit()

        result = await validate_export(db_session, 2025)
        assert any("Betrag" in w for w in result["warnings"])
