import json
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditLog
from app.models.document import DocumentStatus, DocumentType, ReviewStatus, Tag
from app.models.warranty_info import WarrantyInfo, WarrantyType
from tests.factories import make_document as _create_test_document


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

        rows = (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.document_id == doc.id,
                    AuditLog.action == AuditAction.UPDATED,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        details = json.loads(rows[0].details)
        assert details["title"] == {"old": "Testrechnung", "new": "Geaendert"}

    async def test_update_without_change_writes_no_audit_log(
        self, client, db_session: AsyncSession
    ):
        doc = await _create_test_document(db_session)
        await db_session.commit()

        resp = await client.patch(f"/api/documents/{doc.id}", json={"title": "Testrechnung"})
        assert resp.status_code == 200

        rows = (
            await db_session.execute(select(AuditLog).where(AuditLog.document_id == doc.id))
        ).scalars().all()
        assert rows == []


class TestDocumentDeleteEndpoint:
    async def test_soft_delete(self, client, db_session: AsyncSession):
        doc = await _create_test_document(db_session)
        await db_session.commit()

        resp = await client.delete(f"/api/documents/{doc.id}")
        assert resp.status_code == 204
        assert resp.text == ""

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
        await db_session.commit()
        # Tag per API zuweisen (vermeidet Lazy-Loading-Probleme)
        add_resp = await client.post(f"/api/documents/{doc.id}/tags", json={"name": "testag"})
        tag = next(t for t in add_resp.json()["tags"] if t["name"] == "testag")

        resp = await client.delete(f"/api/documents/{doc.id}/tags/{tag['id']}")
        assert resp.status_code == 200

        detail = await client.get(f"/api/documents/{doc.id}")
        assert all(t["name"] != "testag" for t in detail.json()["tags"])

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
        assert resp.json() == {
            "total_documents": 0,
            "documents_this_month": 0,
            "pending_reviews": 0,
            "expiring_warranties_30d": 0,
            "processing_jobs": 0,
        }

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

    async def test_stats_this_month_and_expiring_warranties(
        self, client, db_session: AsyncSession
    ):
        today = date.today()
        # Heute 12:00 UTC liegt in jeder europaeischen Zeitzone im aktuellen Monat.
        this_month = datetime.combine(today, time(12, 0), tzinfo=timezone.utc)
        soon = await _create_test_document(db_session, created_at=this_month)
        later = await _create_test_document(db_session, created_at=this_month)
        await _create_test_document(
            db_session, created_at=datetime(2000, 1, 15, 12, 0, tzinfo=timezone.utc)
        )
        for doc, days_left in [(soon, 10), (later, 90)]:
            db_session.add(
                WarrantyInfo(
                    document_id=doc.id,
                    product_name=f"Produkt {days_left}",
                    purchase_date=today - timedelta(days=30),
                    warranty_end_date=today + timedelta(days=days_left),
                    warranty_type=WarrantyType.LEGAL,
                )
            )
        await db_session.commit()

        data = (await client.get("/api/stats")).json()
        assert data["total_documents"] == 3
        assert data["documents_this_month"] == 2
        assert data["expiring_warranties_30d"] == 1


    # Review-Endpoints wurden nach api/review.py verschoben (siehe test_review.py)
