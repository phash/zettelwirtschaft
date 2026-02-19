from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus


async def _create_doc(db: AsyncSession, **overrides) -> Document:
    defaults = dict(
        original_filename="test.pdf",
        stored_filename="abc_test.pdf",
        file_path="/archive/test.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        file_hash=f"hash_{id(overrides)}_{overrides.get('title', '')}",
        document_type=DocumentType.RECHNUNG,
        title="Testrechnung",
        issuer="Test GmbH",
        ocr_text="OCR Text",
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


class TestSearchEndpoint:
    async def test_search_empty(self, client):
        resp = await client.get("/api/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []

    async def test_search_with_filter(self, client, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", title="Rechnung A")
        await _create_doc(
            db_session, file_hash="h2", title="Quittung B",
            document_type=DocumentType.QUITTUNG,
        )
        await db_session.commit()

        resp = await client.get("/api/search?document_type=RECHNUNG")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestSuggestEndpoint:
    async def test_suggest(self, client, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", issuer="Telekom AG")
        await db_session.commit()

        resp = await client.get("/api/search/suggest?q=Tele")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["suggestions"]) >= 1

    async def test_suggest_too_short(self, client):
        resp = await client.get("/api/search/suggest?q=a")
        assert resp.status_code == 422


class TestSavedSearchEndpoints:
    async def test_create_and_list(self, client):
        resp = await client.post(
            "/api/saved-searches",
            json={"name": "Telekomrechnungen", "query_params": {"q": "Telekom"}},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Telekomrechnungen"

        list_resp = await client.get("/api/saved-searches")
        assert len(list_resp.json()) == 1

    async def test_delete_saved_search(self, client):
        resp = await client.post(
            "/api/saved-searches",
            json={"name": "Test", "query_params": {"q": "test"}},
        )
        search_id = resp.json()["id"]

        del_resp = await client.delete(f"/api/saved-searches/{search_id}")
        assert del_resp.status_code == 200

        list_resp = await client.get("/api/saved-searches")
        assert len(list_resp.json()) == 0
