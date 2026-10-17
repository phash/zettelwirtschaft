import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus, DocumentTag, DocumentType, Tag
from app.models.filing_scope import FilingScope
from app.services.search_service import (
    HIGHLIGHT_END,
    HIGHLIGHT_START,
    InvalidSearchQueryError,
    build_fts_query,
    ensure_fts_table,
    rebuild_fts_index,
    reindex_document_fts,
    remove_document_from_index,
    search_documents,
    suggest,
)
from tests.factories import make_document as _create_doc


async def _tag(db: AsyncSession, doc: Document | None, name: str) -> Tag:
    tag = Tag(name=name, is_auto_generated=False)
    db.add(tag)
    await db.flush()
    if doc is not None:
        db.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
        await db.flush()
    return tag


async def _create_indexed(db: AsyncSession, **overrides) -> Document:
    doc = await _create_doc(db, **overrides)
    await reindex_document_fts(db, doc.id)
    await db.commit()
    return doc


class TestBuildFtsQuery:
    def test_empty(self):
        assert build_fts_query("") == ""
        assert build_fts_query("   ") == ""
        assert build_fts_query(None) == ""

    def test_single_word(self):
        assert build_fts_query("Rechnung") == '"Rechnung"*'

    def test_multiple_words_are_anded(self):
        assert build_fts_query("Telekom Rechnung") == '"Telekom"* AND "Rechnung"*'

    def test_trailing_star_is_not_duplicated(self):
        assert build_fts_query("tele*") == '"tele"*'

    def test_phrase_search_is_exact(self):
        assert build_fts_query('"Deutsche Telekom"') == '"Deutsche Telekom"'

    def test_punctuation_splits_tokens_like_unicode61(self):
        assert build_fts_query("RE-2024-0815") == '"RE 2024 0815"*'
        assert build_fts_query("01.02.2024") == '"01 02 2024"*'
        assert build_fts_query("119,00") == '"119 00"*'

    def test_operators_and_columns_are_literal_text(self):
        assert build_fts_query("Not") == '"Not"*'
        assert build_fts_query("title:foo") == '"title foo"*'
        assert build_fts_query("NEAR(a b)") == '"NEAR a"* AND "b"*'

    def test_only_punctuation_yields_empty(self):
        assert build_fts_query("*") == ""
        assert build_fts_query("- * \"") == ""

    def test_stray_quote_is_dropped(self):
        assert build_fts_query('"Rechnung') == '"Rechnung"*'

    def test_uppercase_or_groups_neighbours(self):
        assert build_fts_query("Telekom Strom OR Gas") == '"Telekom"* AND ("Strom"* OR "Gas"*)'
        # Fuehrende/abschliessende Operatoren werden ignoriert
        assert build_fts_query("OR Strom OR") == '"Strom"*'
        assert build_fts_query("Strom AND Gas") == '"Strom"* AND "Gas"*'


# Jede Eingabe muss durch einen echten FTS5-MATCH laufen und den Treffer finden.
_FTS_DOC = (
    "Rechnung RE-2024-0815 Müller-Lüdenscheidt",
    "Betrag 119,00 EUR am 01.02.2024 per E-Mail. Not a title: foo",
)


@pytest.mark.parametrize(
    ("raw", "should_match"),
    [
        ("E-Mail", True),
        ("RE-2024-0815", True),
        ("01.02.2024", True),
        ("119,00", True),
        ('"Rechnung', True),
        ("Not", True),
        ("title:foo", True),
        ("Müller-Lüdenscheidt", True),
        ('"119,00 EUR"', True),
        ("Rechnung OR Quatsch", True),
        ("-Rechnung", True),
        ("Quatsch", False),
    ],
)
def test_build_fts_query_runs_on_real_fts5(raw, should_match):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE f USING fts5(title, ocr_text, "
        "tokenize='unicode61 remove_diacritics 2')"
    )
    conn.execute("INSERT INTO f VALUES (?, ?)", _FTS_DOC)
    rows = conn.execute("SELECT 1 FROM f WHERE f MATCH ?", (build_fts_query(raw),)).fetchall()
    assert bool(rows) is should_match


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
            document_types=["RECHNUNG"],
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

        await reindex_document_fts(db_session, doc.id)
        await db_session.commit()

        result = await search_documents(
            session=db_session,
            query="Telekom",
        )
        assert result["total"] == 1
        assert "Telekom" in result["results"][0]["title"]

    @pytest.mark.parametrize(
        "query",
        ["E-Mail", "RE-2024-0815", "01.02.2024", "119,00", "Müller-Lüdenscheidt", '"Rechnung'],
    )
    async def test_search_punctuated_queries_find_document(self, db_session: AsyncSession, query):
        await _create_indexed(
            db_session, file_hash="h1",
            title="Rechnung RE-2024-0815",
            issuer="Müller-Lüdenscheidt GmbH",
            ocr_text="Betrag 119,00 EUR, faellig am 01.02.2024, Versand per E-Mail",
        )
        result = await search_documents(session=db_session, query=query)
        assert result["total"] == 1

    async def test_query_without_searchable_terms_returns_nothing(self, db_session: AsyncSession):
        await _create_indexed(db_session, file_hash="h1", title="Rechnung")
        result = await search_documents(session=db_session, query="* -")
        assert result["total"] == 0
        assert result["results"] == []
        assert result["facets"]["document_types"] == {}

    async def test_invalid_match_raises_domain_error(self, db_session: AsyncSession, monkeypatch):
        """Defense in depth: FTS5-Syntaxfehler werden zu InvalidSearchQueryError."""
        await _create_indexed(db_session, file_hash="h1", title="Rechnung")
        monkeypatch.setattr(
            "app.services.search_service.build_fts_query", lambda q: '"kaputt'
        )
        with pytest.raises(InvalidSearchQueryError):
            await search_documents(session=db_session, query="egal")

    async def test_highlight_uses_sentinels_and_best_column(self, db_session: AsyncSession):
        await _create_indexed(
            db_session, file_hash="h1",
            title="Stromabrechnung Stadtwerke",
            ocr_text="Voellig anderer Text ohne Suchbegriff",
        )
        result = await search_documents(session=db_session, query="Stromabrechnung")
        highlight = result["results"][0]["highlight"]
        assert f"{HIGHLIGHT_START}Stromabrechnung{HIGHLIGHT_END}" in highlight
        assert "<mark>" not in highlight

    async def test_amount_is_decimal_compatible(self, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", amount=Decimal("119.99"))
        await db_session.commit()
        result = await search_documents(session=db_session)
        assert Decimal(str(result["results"][0]["amount"])) == Decimal("119.99")
        assert "thumbnail_path" not in result["results"][0]

    async def test_search_excludes_deleted(self, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", title="Aktiv")
        await _create_doc(
            db_session, file_hash="h2", title="Geloescht",
            status=DocumentStatus.DELETED,
        )
        await db_session.commit()

        result = await search_documents(session=db_session)
        assert result["total"] == 1

    async def test_date_filter_is_inclusive(self, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", document_date=date(2024, 12, 31))
        await _create_doc(db_session, file_hash="h2", document_date=date(2025, 1, 1))
        await db_session.commit()
        result = await search_documents(
            session=db_session, date_from=date(2024, 1, 1), date_to=date(2024, 12, 31),
        )
        assert result["total"] == 1

    async def test_issuer_filter_escapes_like_wildcards(self, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", issuer="Telekom")
        await _create_doc(db_session, file_hash="h2", issuer="A_B Handel")
        await _create_doc(db_session, file_hash="h3", issuer="AxB Handel")
        await db_session.commit()

        assert (await search_documents(session=db_session, issuer="%"))["total"] == 0
        result = await search_documents(session=db_session, issuer="A_B")
        assert [r["issuer"] for r in result["results"]] == ["A_B Handel"]

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

    async def test_type_facet_is_disjunctive(self, db_session: AsyncSession):
        """Mit Typ-Filter RECHNUNG bleiben die anderen Typen in der Facette sichtbar."""
        await _create_doc(db_session, file_hash="h1", document_type=DocumentType.RECHNUNG)
        await _create_doc(db_session, file_hash="h2", document_type=DocumentType.QUITTUNG)
        await _create_doc(
            db_session, file_hash="h3", document_type=DocumentType.QUITTUNG,
            tax_relevant=True,
        )
        await db_session.commit()

        result = await search_documents(session=db_session, document_types=["RECHNUNG"])
        assert result["total"] == 1
        assert result["facets"]["document_types"] == {"RECHNUNG": 1, "QUITTUNG": 2}
        # Andere Filter wirken weiterhin auf die Facette
        result = await search_documents(
            session=db_session, document_types=["RECHNUNG"], tax_relevant=True,
        )
        assert result["facets"]["document_types"] == {"QUITTUNG": 1}

    async def test_scope_facet_keyed_by_id(self, db_session: AsyncSession):
        scope_a = FilingScope(name="Privat", slug="privat")
        scope_b = FilingScope(name="Praxis", slug="praxis")
        db_session.add_all([scope_a, scope_b])
        await db_session.flush()
        await _create_doc(db_session, file_hash="h1", filing_scope_id=scope_a.id)
        await _create_doc(db_session, file_hash="h2", filing_scope_id=scope_a.id)
        await _create_doc(db_session, file_hash="h3", filing_scope_id=scope_b.id)
        await db_session.commit()

        result = await search_documents(session=db_session, filing_scope_id=scope_b.id)
        assert result["total"] == 1
        assert result["facets"]["filing_scopes"] == [
            {"id": scope_a.id, "name": "Privat", "count": 2},
            {"id": scope_b.id, "name": "Praxis", "count": 1},
        ]

    async def test_facets_with_fts(self, db_session: AsyncSession):
        await _create_indexed(db_session, file_hash="h1", title="Strom Januar")
        await _create_indexed(
            db_session, file_hash="h2", title="Strom Februar",
            document_type=DocumentType.QUITTUNG,
        )
        await _create_indexed(db_session, file_hash="h3", title="Wasser")
        result = await search_documents(
            session=db_session, query="Strom", document_types=["RECHNUNG"],
        )
        assert result["total"] == 1
        assert result["facets"]["document_types"] == {"RECHNUNG": 1, "QUITTUNG": 1}

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


class TestFtsIndexHelpers:
    async def _fts_row(self, db: AsyncSession, doc_id: str):
        result = await db.execute(
            text("SELECT title, tags FROM documents_fts WHERE doc_id = :id"), {"id": doc_id}
        )
        return result.fetchall()

    async def test_reindex_reads_tags_from_db(self, db_session: AsyncSession):
        doc = await _create_doc(db_session, file_hash="h1", title="Alt")
        await _tag(db_session, doc, "strom")
        await reindex_document_fts(db_session, doc.id)
        await db_session.commit()
        assert await self._fts_row(db_session, doc.id) == [("Alt", "strom")]

        # Aenderung + neuer Tag -> Reindex ersetzt den Eintrag (kein Duplikat)
        doc.title = "Neu"
        await _tag(db_session, doc, "energie")
        await reindex_document_fts(db_session, doc.id)
        await db_session.commit()
        rows = await self._fts_row(db_session, doc.id)
        assert len(rows) == 1
        assert rows[0][0] == "Neu"
        assert set(rows[0][1].split()) == {"strom", "energie"}
        assert (await search_documents(session=db_session, query="energie"))["total"] == 1

    async def test_reindex_skips_deleted(self, db_session: AsyncSession):
        doc = await _create_indexed(db_session, file_hash="h1", title="Weg")
        doc.status = DocumentStatus.DELETED
        await reindex_document_fts(db_session, doc.id)
        await db_session.commit()
        assert await self._fts_row(db_session, doc.id) == []

    async def test_remove_document_from_index(self, db_session: AsyncSession):
        doc = await _create_indexed(db_session, file_hash="h1", title="Weg")
        await remove_document_from_index(db_session, doc.id)
        await db_session.commit()
        assert await self._fts_row(db_session, doc.id) == []

    async def test_rebuild_matches_reindex(self, db_session: AsyncSession):
        doc = await _create_doc(db_session, file_hash="h1", title="Alt")
        await _tag(db_session, doc, "strom")
        await _create_doc(db_session, file_hash="h2", status=DocumentStatus.DELETED)
        await db_session.commit()
        assert await rebuild_fts_index(db_session) == 1
        assert await self._fts_row(db_session, doc.id) == [("Alt", "strom")]


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

    async def test_suggest_escapes_like_wildcards(self, db_session: AsyncSession):
        await _create_doc(db_session, file_hash="h1", issuer="Telekom", title="Rechnung")
        await db_session.commit()
        assert await suggest(db_session, "%%") == []
        assert await suggest(db_session, "__") == []

    async def test_suggest_only_tags_of_active_documents(self, db_session: AsyncSession):
        active = await _create_doc(db_session, file_hash="h1", issuer=None, title="x")
        deleted = await _create_doc(
            db_session, file_hash="h2", issuer=None, title="y", status=DocumentStatus.DELETED,
        )
        await _tag(db_session, active, "stromvertrag")
        await _tag(db_session, deleted, "stromzaehler")
        await _tag(db_session, None, "stromanbieter")  # verwaist
        await db_session.commit()

        assert await suggest(db_session, "strom") == ["stromvertrag"]
