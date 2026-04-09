"""Tests fuer Garantie-Tracker API."""

import pytest
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus
from app.models.warranty_info import WarrantyInfo
from app.models.filing_scope import FilingScope


async def _create_filing_scope(db: AsyncSession) -> FilingScope:
    """Erstellt einen Filing Scope fuer Dokument-FK."""
    scope = FilingScope(
        name="Privat",
        slug="privat",
        is_default=True,
    )
    db.add(scope)
    await db.flush()
    return scope


async def _create_doc_with_warranty(
    db: AsyncSession,
    scope_id: str,
    *,
    file_hash: str,
    title: str = "Testdokument",
    status: DocumentStatus = DocumentStatus.ACTIVE,
    product_name: str = "Testprodukt",
    purchase_date: date | None = None,
    warranty_end_date: date | None = None,
    warranty_type: str = "LEGAL",
    retailer: str | None = "TestShop",
    notes: str | None = None,
) -> tuple[Document, WarrantyInfo]:
    """Erstellt ein Dokument mit Garantie-Info."""
    if purchase_date is None:
        purchase_date = date.today() - timedelta(days=180)
    if warranty_end_date is None:
        warranty_end_date = date.today() + timedelta(days=365)

    doc = Document(
        original_filename="test.pdf",
        stored_filename="abc_test.pdf",
        file_path="/archive/test.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        file_hash=file_hash,
        document_type=DocumentType.KAUFVERTRAG,
        title=title,
        issuer="Test GmbH",
        ocr_text="OCR Text",
        ocr_confidence=0.9,
        ai_confidence=0.92,
        status=status,
        review_status=ReviewStatus.OK,
        filing_scope_id=scope_id,
    )
    db.add(doc)
    await db.flush()

    warranty = WarrantyInfo(
        document_id=doc.id,
        product_name=product_name,
        purchase_date=purchase_date,
        warranty_end_date=warranty_end_date,
        warranty_type=warranty_type,
        warranty_duration_months=24,
        retailer=retailer,
        notes=notes,
    )
    db.add(warranty)
    await db.flush()
    return doc, warranty


@pytest.mark.asyncio
class TestWarrantyList:
    async def test_list_empty(self, client):
        resp = await client.get("/api/warranties")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_filter_active(self, client):
        resp = await client.get("/api/warranties?status=active")
        assert resp.status_code == 200

    async def test_list_filter_expired(self, client):
        resp = await client.get("/api/warranties?status=expired")
        assert resp.status_code == 200

    async def test_list_with_data(self, client, db_session: AsyncSession):
        scope = await _create_filing_scope(db_session)
        # Aktive Garantie (Enddatum in der Zukunft)
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_active",
            title="Laptop",
            product_name="ThinkPad X1",
            warranty_end_date=date.today() + timedelta(days=365),
        )
        # Abgelaufene Garantie (Enddatum in der Vergangenheit)
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_expired",
            title="Drucker",
            product_name="HP LaserJet",
            warranty_end_date=date.today() - timedelta(days=30),
        )
        await db_session.commit()

        resp = await client.get("/api/warranties")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_list_excludes_deleted(self, client, db_session: AsyncSession):
        scope = await _create_filing_scope(db_session)
        # Aktives Dokument mit Garantie
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_active_doc",
            title="Aktives Geraet",
            product_name="Monitor",
        )
        # Geloeschtes Dokument mit Garantie
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_deleted_doc",
            title="Geloeschtes Geraet",
            product_name="Alte Maus",
            status=DocumentStatus.DELETED,
        )
        await db_session.commit()

        resp = await client.get("/api/warranties")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["product_name"] == "Monitor"

    async def test_list_filter_active_with_data(self, client, db_session: AsyncSession):
        scope = await _create_filing_scope(db_session)
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_act",
            product_name="Aktiv-Produkt",
            warranty_end_date=date.today() + timedelta(days=200),
        )
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_exp",
            product_name="Abgelaufen-Produkt",
            warranty_end_date=date.today() - timedelta(days=10),
        )
        await db_session.commit()

        resp = await client.get("/api/warranties?status=active")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["product_name"] == "Aktiv-Produkt"

    async def test_list_filter_expired_with_data(self, client, db_session: AsyncSession):
        scope = await _create_filing_scope(db_session)
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_act2",
            product_name="Noch gueltig",
            warranty_end_date=date.today() + timedelta(days=100),
        )
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_exp2",
            product_name="Laengst abgelaufen",
            warranty_end_date=date.today() - timedelta(days=60),
        )
        await db_session.commit()

        resp = await client.get("/api/warranties?status=expired")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["product_name"] == "Laengst abgelaufen"

    async def test_list_response_fields(self, client, db_session: AsyncSession):
        scope = await _create_filing_scope(db_session)
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_fields",
            title="Mein Laptop",
            product_name="Dell XPS 15",
            warranty_end_date=date.today() + timedelta(days=180),
            retailer="MediaMarkt",
        )
        await db_session.commit()

        resp = await client.get("/api/warranties")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        item = data[0]
        assert item["document_title"] == "Mein Laptop"
        assert item["days_remaining"] > 0
        assert item["product_name"] == "Dell XPS 15"
        assert item["retailer"] == "MediaMarkt"
        assert item["warranty_type"] == "LEGAL"
        assert "id" in item
        assert "document_id" in item
        assert "purchase_date" in item
        assert "warranty_end_date" in item
        assert "is_expired" in item


@pytest.mark.asyncio
class TestWarrantyStats:
    async def test_stats_empty(self, client):
        resp = await client.get("/api/warranties/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["active"] == 0
        assert data["expiring_soon"] == 0
        assert data["expired"] == 0

    async def test_stats_with_data(self, client, db_session: AsyncSession):
        scope = await _create_filing_scope(db_session)
        # Aktive Garantie (weit in der Zukunft -> nicht "expiring_soon")
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="s_active",
            product_name="Aktiv",
            warranty_end_date=date.today() + timedelta(days=200),
        )
        # Abgelaufene Garantie
        await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="s_expired",
            product_name="Abgelaufen",
            warranty_end_date=date.today() - timedelta(days=30),
        )
        await db_session.commit()

        resp = await client.get("/api/warranties/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["active"] == 1
        assert data["expired"] == 1


@pytest.mark.asyncio
class TestWarrantyUpdate:
    async def test_update_not_found(self, client):
        resp = await client.patch("/api/warranties/nonexistent", json={"notes": "test"})
        assert resp.status_code == 404

    async def test_update_warranty(self, client, db_session: AsyncSession):
        scope = await _create_filing_scope(db_session)
        _, warranty = await _create_doc_with_warranty(
            db_session, scope.id,
            file_hash="w_update",
            product_name="Update-Produkt",
            notes=None,
        )
        await db_session.commit()

        resp = await client.patch(
            f"/api/warranties/{warranty.id}",
            json={"notes": "Garantie beim Haendler verlaengert"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notes"] == "Garantie beim Haendler verlaengert"
        assert data["product_name"] == "Update-Produkt"
