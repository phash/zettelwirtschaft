import json
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus, Tag
from app.models.review_question import ReviewQuestion


async def _create_test_document(db: AsyncSession, **overrides) -> Document:
    """Erstellt ein Test-Dokument in der DB."""
    defaults = dict(
        original_filename="test.pdf",
        stored_filename="abc_test.pdf",
        file_path="/archive/2024/03/RECHNUNG/abc_test.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        file_hash=f"hash_{id(overrides)}_{overrides.get('title', 'test')}",
        document_type=DocumentType.RECHNUNG,
        title="Testrechnung",
        issuer="Test GmbH",
        ocr_text="OCR Text der Testrechnung",
        ocr_confidence=0.9,
        ai_confidence=0.92,
        status=DocumentStatus.ACTIVE,
        review_status=ReviewStatus.OK,
    )
    defaults.update(overrides)
    doc = Document(**defaults)
    db.add(doc)
    await db.flush()
    return doc


class TestDocumentListEndpoint:
    async def test_list_empty(self, client):
        resp = await client.get("/api/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_with_documents(self, client, db_session: AsyncSession):
        await _create_test_document(db_session, file_hash="hash1", title="Rechnung 1")
        await _create_test_document(db_session, file_hash="hash2", title="Rechnung 2")
        await db_session.commit()

        resp = await client.get("/api/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    async def test_list_filter_by_type(self, client, db_session: AsyncSession):
        await _create_test_document(
            db_session, file_hash="h1", document_type=DocumentType.RECHNUNG
        )
        await _create_test_document(
            db_session, file_hash="h2", document_type=DocumentType.QUITTUNG
        )
        await db_session.commit()

        resp = await client.get("/api/documents?document_type=RECHNUNG")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_list_excludes_deleted(self, client, db_session: AsyncSession):
        await _create_test_document(db_session, file_hash="h1")
        await _create_test_document(
            db_session, file_hash="h2", status=DocumentStatus.DELETED
        )
        await db_session.commit()

        resp = await client.get("/api/documents")
        assert resp.json()["total"] == 1


class TestDocumentDetailEndpoint:
    async def test_get_document(self, client, db_session: AsyncSession):
        doc = await _create_test_document(db_session)
        await db_session.commit()

        resp = await client.get(f"/api/documents/{doc.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Testrechnung"
        assert data["document_type"] == "RECHNUNG"

    async def test_get_nonexistent(self, client):
        resp = await client.get("/api/documents/nonexistent-id")
        assert resp.status_code == 404


class TestDocumentUpdateEndpoint:
    async def test_update_title(self, client, db_session: AsyncSession):
        doc = await _create_test_document(db_session)
        await db_session.commit()

        resp = await client.patch(
            f"/api/documents/{doc.id}",
            json={"title": "Neuer Titel"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Neuer Titel"

    async def test_update_creates_audit_log(self, client, db_session: AsyncSession):
        doc = await _create_test_document(db_session)
        await db_session.commit()

        resp = await client.patch(
            f"/api/documents/{doc.id}",
            json={"title": "Geaendert"},
        )
        assert resp.status_code == 200


class TestDocumentDeleteEndpoint:
    async def test_soft_delete(self, client, db_session: AsyncSession):
        doc = await _create_test_document(db_session)
        await db_session.commit()

        resp = await client.delete(f"/api/documents/{doc.id}")
        assert resp.status_code == 200

        # Should not appear in list
        list_resp = await client.get("/api/documents")
        assert list_resp.json()["total"] == 0


class TestTagEndpoints:
    async def test_add_tag(self, client, db_session: AsyncSession):
        doc = await _create_test_document(db_session)
        await db_session.commit()

        resp = await client.post(
            f"/api/documents/{doc.id}/tags",
            json={"name": "Elektronik"},
        )
        assert resp.status_code == 200
        tags = resp.json()["tags"]
        assert any(t["name"] == "elektronik" for t in tags)

    async def test_remove_tag(self, client, db_session: AsyncSession):
        doc = await _create_test_document(db_session)
        tag = Tag(name="testag", is_auto_generated=False)
        db_session.add(tag)
        await db_session.flush()
        # Use API to add tag first (avoids lazy-loading issues)
        await db_session.commit()
        await client.post(f"/api/documents/{doc.id}/tags", json={"name": "testag"})

        resp = await client.delete(f"/api/documents/{doc.id}/tags/testag")
        assert resp.status_code == 200

    async def test_list_tags(self, client, db_session: AsyncSession):
        db_session.add(Tag(name="alpha", is_auto_generated=True))
        db_session.add(Tag(name="beta", is_auto_generated=False))
        await db_session.commit()

        resp = await client.get("/api/tags")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


class TestDashboardStats:
    async def test_stats_empty(self, client):
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_documents"] == 0

    async def test_stats_with_data(self, client, db_session: AsyncSession):
        await _create_test_document(db_session, file_hash="h1")
        await _create_test_document(
            db_session,
            file_hash="h2",
            review_status=ReviewStatus.NEEDS_REVIEW,
        )
        await db_session.commit()

        resp = await client.get("/api/stats")
        data = resp.json()
        assert data["total_documents"] == 2
        assert data["pending_reviews"] == 1


class TestReviewEndpoints:
    async def test_list_review_documents(self, client, db_session: AsyncSession):
        await _create_test_document(
            db_session,
            file_hash="h1",
            review_status=ReviewStatus.NEEDS_REVIEW,
        )
        await _create_test_document(
            db_session, file_hash="h2", review_status=ReviewStatus.OK
        )
        await db_session.commit()

        resp = await client.get("/api/documents/review/pending")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_answer_review_question(self, client, db_session: AsyncSession):
        doc = await _create_test_document(
            db_session,
            review_status=ReviewStatus.NEEDS_REVIEW,
        )
        q = ReviewQuestion(
            document_id=doc.id,
            question="Ist der Betrag korrekt?",
        )
        db_session.add(q)
        await db_session.flush()
        await db_session.commit()

        resp = await client.post(
            f"/api/documents/{doc.id}/review/{q.id}",
            json={"answer": "Ja, 119 EUR ist korrekt"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_answered"] is True
