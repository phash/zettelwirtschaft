from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentStatus, DocumentType
from app.services.search_service import reindex_document_fts
from tests.factories import make_document as _create_doc


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

    async def test_result_timestamps_carry_utc_offset(self, client, db_session: AsyncSession):
        """Rohes SQL der Suche liefert created_at mit Offset wie alle anderen Endpoints."""
        from datetime import datetime, timezone

        doc = await _create_doc(db_session, title="Rechnung A", ocr_text="Stromrechnung")
        await reindex_document_fts(db_session, doc.id)
        await db_session.commit()

        for params in ({}, {"q": "Stromrechnung"}):
            [item] = (await client.get("/api/search", params=params)).json()["results"]
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            assert created.utcoffset() == timezone.utc.utcoffset(None), item["created_at"]

    @pytest.mark.parametrize("q", ["E-Mail", "RE-2024-0815", "*", '"Rechnung', "-Rechnung", "119,00"])
    async def test_fulltext_with_punctuation_returns_200(self, client, db_session: AsyncSession, q):
        doc = await _create_doc(
            db_session, file_hash="h1", title="Rechnung RE-2024-0815",
            ocr_text="Betrag 119,00 EUR per E-Mail",
        )
        await reindex_document_fts(db_session, doc.id)
        await db_session.commit()

        resp = await client.get("/api/search", params={"q": q})
        assert resp.status_code == 200
        expected = 0 if q == "*" else 1
        assert resp.json()["total"] == expected

    async def test_result_has_no_filesystem_path(self, client, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", thumbnail_path="/data/thumbnails/x.png")
        await db_session.commit()
        item = (await client.get("/api/search")).json()["results"][0]
        assert "thumbnail_path" not in item

    async def test_scope_facet_is_list(self, client):
        data = (await client.get("/api/search")).json()
        assert data["facets"]["filing_scopes"] == []

    async def test_invalid_fts_expression_returns_400(self, client, monkeypatch):
        monkeypatch.setattr("app.services.search_service.build_fts_query", lambda q: '"kaputt')
        resp = await client.get("/api/search", params={"q": "egal"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Ungültige Suchanfrage"

    async def test_empty_amount_filter_is_ignored(self, client, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1")
        await db_session.commit()
        resp = await client.get("/api/search?amount_min=&amount_max=&date_from=&tax_year=")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_date_to_includes_whole_day(self, client, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", document_date=date(2024, 6, 1))
        await db_session.commit()
        resp = await client.get("/api/search?date_from=2024-01-01&date_to=2024-12-31")
        assert resp.json()["total"] == 1

    @pytest.mark.parametrize(
        "query",
        ["date_to=2024", "date_from=gestern", "status=active", "document_type=RECHNUNG,FOO", "tax_category=Quatsch"],
    )
    async def test_invalid_filters_return_422(self, client, query):
        resp = await client.get(f"/api/search?{query}")
        assert resp.status_code == 422

    async def test_status_filter(self, client, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", status=DocumentStatus.ARCHIVED)
        await _create_doc(db_session, file_hash="h2")
        await db_session.commit()
        resp = await client.get("/api/search?status=ARCHIVED")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_multiple_document_types(self, client, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1")
        await _create_doc(db_session, file_hash="h2", document_type=DocumentType.QUITTUNG)
        await _create_doc(db_session, file_hash="h3", document_type=DocumentType.MIETVERTRAG)
        await db_session.commit()
        resp = await client.get("/api/search?document_type=RECHNUNG, QUITTUNG,")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["facets"]["document_types"]["MIETVERTRAG"] == 1


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
        assert resp.status_code == 201
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

    async def test_delete_unknown_saved_search_returns_404(self, client):
        resp = await client.delete("/api/saved-searches/does-not-exist")
        assert resp.status_code == 404
