from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus
from app.services.search_service import (
    _sanitize_fts_query,
    ensure_fts_table,
    index_document,
    search_documents,
    suggest,
)


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


class TestSanitizeFtsQuery:
    def test_empty(self):
        assert _sanitize_fts_query("") == ""
        assert _sanitize_fts_query("   ") == ""

    def test_single_word(self):
        assert _sanitize_fts_query("Rechnung") == "Rechnung*"

    def test_multiple_words(self):
        result = _sanitize_fts_query("Telekom Rechnung")
        assert "Telekom*" in result
        assert "Rechnung*" in result

    def test_preserves_prefix(self):
        assert _sanitize_fts_query("tele*") == "tele*"

    def test_phrase_search(self):
        result = _sanitize_fts_query('"Deutsche Telekom"')
        assert '"Deutsche Telekom"' in result


class TestSearchDocuments:
    async def test_search_without_fts(self, db_session: AsyncSession):
        """Suche ohne Volltextquery - nur Metadatenfilter."""
        await _create_doc(db_session, file_hash="h1", title="Rechnung 1")
        await _create_doc(
            db_session, file_hash="h2", title="Quittung 1",
            document_type=DocumentType.QUITTUNG,
        )
        await db_session.commit()

        result = await search_documents(
            session=db_session,
            document_type="RECHNUNG",
        )
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Rechnung 1"

    async def test_search_with_fts(self, db_session: AsyncSession):
        """Volltextsuche mit FTS5-Index."""
        await ensure_fts_table(db_session)

        doc = await _create_doc(
            db_session, file_hash="h1",
            title="Telekom Rechnung Maerz",
            ocr_text="Monatliche Rechnung fuer Festnetz und Internet",
            issuer="Deutsche Telekom AG",
        )
        await db_session.commit()

        await index_document(
            session=db_session,
            doc_id=doc.id,
            title=doc.title,
            ocr_text=doc.ocr_text,
            issuer=doc.issuer,
            summary=None,
            tags="telekommunikation",
        )
        await db_session.commit()

        result = await search_documents(
            session=db_session,
            query="Telekom",
        )
        assert result["total"] == 1
        assert "Telekom" in result["results"][0]["title"]

    async def test_search_excludes_deleted(self, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", title="Aktiv")
        await _create_doc(
            db_session, file_hash="h2", title="Geloescht",
            status=DocumentStatus.DELETED,
        )
        await db_session.commit()

        result = await search_documents(session=db_session)
        assert result["total"] == 1

    async def test_search_facets(self, db_session: AsyncSession):
        await _create_doc(
            db_session, file_hash="h1",
            document_type=DocumentType.RECHNUNG,
            issuer="Telekom",
        )
        await _create_doc(
            db_session, file_hash="h2",
            document_type=DocumentType.RECHNUNG,
            issuer="Telekom",
        )
        await _create_doc(
            db_session, file_hash="h3",
            document_type=DocumentType.QUITTUNG,
            issuer="Amazon",
        )
        await db_session.commit()

        result = await search_documents(session=db_session)
        facets = result["facets"]
        assert facets["document_types"]["RECHNUNG"] == 2
        assert facets["document_types"]["QUITTUNG"] == 1
        assert facets["top_issuers"]["Telekom"] == 2

    async def test_search_pagination(self, db_session: AsyncSession):
        for i in range(5):
            await _create_doc(
                db_session, file_hash=f"h{i}", title=f"Doc {i}",
            )
        await db_session.commit()

        result = await search_documents(
            session=db_session, page=1, page_size=2,
        )
        assert result["total"] == 5
        assert len(result["results"]) == 2


class TestSuggest:
    async def test_suggest_issuers(self, db_session: AsyncSession):
        await _create_doc(
            db_session, file_hash="h1", issuer="Deutsche Telekom AG",
        )
        await _create_doc(
            db_session, file_hash="h2", issuer="Telefonica Germany",
        )
        await db_session.commit()

        suggestions = await suggest(db_session, "Tele")
        assert len(suggestions) >= 1
        assert any("Tele" in s for s in suggestions)

    async def test_suggest_short_query(self, db_session: AsyncSession):
        suggestions = await suggest(db_session, "a")
        assert suggestions == []
