"""Tests fuer den RAG-Service."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import date

from app.services.rag_service import ask_question
from app.models.document import Document, DocumentType, DocumentStatus


class TestAskQuestion:
    def _make_doc(self, doc_id="doc-1", title="Testdokument", scope_id=None):
        doc = MagicMock(spec=Document)
        doc.id = doc_id
        doc.title = title
        doc.document_type = DocumentType.RECHNUNG
        doc.document_date = date(2024, 1, 15)
        doc.issuer = "Firma Test"
        doc.filing_scope_id = scope_id
        doc.status = DocumentStatus.ACTIVE
        return doc

    @pytest.mark.asyncio
    async def test_embedding_unavailable(self, test_settings, db_session):
        with patch("app.services.rag_service.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = None
            result = await ask_question("Test?", test_settings, db_session)
            assert "nicht verfuegbar" in result["answer"]
            assert result["sources"] == []
            assert result["chunks_found"] == 0

    @pytest.mark.asyncio
    async def test_no_chunks_found(self, test_settings, db_session):
        with patch("app.services.rag_service.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]
            with patch("app.services.rag_service.search_similar_chunks") as mock_search:
                mock_search.return_value = []
                result = await ask_question("Test?", test_settings, db_session)
                assert "Keine relevanten Dokumente" in result["answer"]
                assert result["chunks_found"] == 0

    @pytest.mark.asyncio
    async def test_successful_answer(self, test_settings, db_session):
        doc = self._make_doc()

        # Dokument in DB anlegen
        from app.models.document import Document as DocModel
        db_doc = DocModel(
            id="doc-1",
            original_filename="test.pdf",
            stored_filename="test.pdf",
            file_path="/tmp/test.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            file_hash="abc123",
            title="Testdokument",
            document_type=DocumentType.RECHNUNG,
            document_date=date(2024, 1, 15),
            issuer="Firma Test",
            ocr_text="Testtext",
            status=DocumentStatus.ACTIVE,
        )
        db_session.add(db_doc)
        await db_session.flush()

        chunks = [
            {
                "id": "doc-1_chunk_0",
                "doc_id": "doc-1",
                "text": "Dies ist ein Testtext ueber Versicherung.",
                "distance": 0.1,
                "metadata": {"doc_id": "doc-1", "title": "Testdokument"},
            },
        ]

        with patch("app.services.rag_service.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]
            with patch("app.services.rag_service.search_similar_chunks") as mock_search:
                mock_search.return_value = chunks
                with patch("app.services.rag_service.call_llm_text", new_callable=AsyncMock) as mock_llm:
                    mock_llm.return_value = "Die Versicherung kostet 100 EUR [Dok. 1]."
                    result = await ask_question("Wie viel kostet die Versicherung?", test_settings, db_session)
                    assert "100 EUR" in result["answer"]
                    assert len(result["sources"]) == 1
                    assert result["sources"][0]["document_id"] == "doc-1"
                    assert result["chunks_found"] == 1

    @pytest.mark.asyncio
    async def test_scope_filter(self, test_settings, db_session):
        # Zwei Dokumente mit verschiedenen Scopes
        doc1 = Document(
            id="doc-scope1",
            original_filename="a.pdf",
            stored_filename="a.pdf",
            file_path="/tmp/a.pdf",
            file_type="pdf",
            file_size_bytes=100,
            file_hash="hash1",
            title="Privat-Dok",
            ocr_text="Text",
            status=DocumentStatus.ACTIVE,
            filing_scope_id="scope-privat",
        )
        doc2 = Document(
            id="doc-scope2",
            original_filename="b.pdf",
            stored_filename="b.pdf",
            file_path="/tmp/b.pdf",
            file_type="pdf",
            file_size_bytes=100,
            file_hash="hash2",
            title="Praxis-Dok",
            ocr_text="Text",
            status=DocumentStatus.ACTIVE,
            filing_scope_id="scope-praxis",
        )
        db_session.add(doc1)
        db_session.add(doc2)
        await db_session.flush()

        # Nach H-13-Fix delegiert rag_service den Scope-Filter an ChromaDB
        # (search_similar_chunks(filing_scope_id=...)). Der Mock gibt entsprechend
        # nur den passenden Chunk zurueck — wenn ein anderer Scope gefiltert wird,
        # liefert ChromaDB nichts.
        def fake_search(query_embedding, settings, top_k=None, filing_scope_id=None):
            if filing_scope_id == "scope-privat":
                return [{"id": "doc-scope1_0", "doc_id": "doc-scope1", "text": "Privat text", "distance": 0.1, "metadata": {"filing_scope_id": "scope-privat"}}]
            return [{"id": "doc-scope2_0", "doc_id": "doc-scope2", "text": "Praxis text", "distance": 0.2, "metadata": {"filing_scope_id": "scope-praxis"}}]

        with patch("app.services.rag_service.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2]
            with patch("app.services.rag_service.search_similar_chunks", side_effect=fake_search) as mock_search:
                with patch("app.services.rag_service.call_llm_text", new_callable=AsyncMock) as mock_llm:
                    mock_llm.return_value = "Antwort basierend auf Privat-Dok."
                    result = await ask_question(
                        "Test?", test_settings, db_session,
                        filing_scope_id="scope-privat",
                    )
                    # search_similar_chunks muss mit dem korrekten Scope-Filter
                    # aufgerufen worden sein (das ist der eigentliche H-13-Fix).
                    call_kwargs = mock_search.call_args.kwargs
                    assert call_kwargs.get("filing_scope_id") == "scope-privat"
                    assert len(result["sources"]) == 1
                    assert result["sources"][0]["document_id"] == "doc-scope1"

    @pytest.mark.asyncio
    async def test_llm_failure(self, test_settings, db_session):
        doc = Document(
            id="doc-llm-fail",
            original_filename="c.pdf",
            stored_filename="c.pdf",
            file_path="/tmp/c.pdf",
            file_type="pdf",
            file_size_bytes=100,
            file_hash="hash_llm_fail",
            title="LLM-Test",
            ocr_text="Text",
            status=DocumentStatus.ACTIVE,
        )
        db_session.add(doc)
        await db_session.flush()

        chunks = [
            {"id": "doc-llm-fail_0", "doc_id": "doc-llm-fail", "text": "Text", "distance": 0.1, "metadata": {}},
        ]

        with patch("app.services.rag_service.embed_text", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2]
            with patch("app.services.rag_service.search_similar_chunks") as mock_search:
                mock_search.return_value = chunks
                with patch("app.services.rag_service.call_llm_text", new_callable=AsyncMock) as mock_llm:
                    mock_llm.return_value = None
                    result = await ask_question("Test?", test_settings, db_session)
                    assert "keine Antwort generieren" in result["answer"]
                    assert result["chunks_found"] == 1


class TestLLMRerank:
    """Tests fuer den optionalen LLM-Reranker (rag_service._llm_rerank)."""

    @pytest.mark.asyncio
    async def test_passthrough_when_chunks_below_target(self, test_settings):
        from app.services.rag_service import _llm_rerank
        chunks = [{"doc_id": "a"}, {"doc_id": "b"}]
        result = await _llm_rerank(chunks, "frage", test_settings, target_k=5)
        assert result == chunks  # nicht mehr als target_k -> nichts zu tun

    @pytest.mark.asyncio
    async def test_score_validation_clamps_invalid_values(self, test_settings):
        from app.services.rag_service import _llm_rerank
        chunks = [
            {"doc_id": "a", "text": "alpha", "_rrf_score": 0.5},
            {"doc_id": "b", "text": "beta", "_rrf_score": 0.4},
            {"doc_id": "c", "text": "gamma", "_rrf_score": 0.3},
        ]
        # NaN, negativ, > 10, null — alle muessen geclamped werden
        with patch("app.services.rag_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = '{"scores": [-5, 100, "invalid"]}'
            result = await _llm_rerank(chunks, "frage", test_settings, target_k=2)
        assert len(result) == 2  # auf target_k gestutzt
        # _rrf_score darf weder negativ noch ueber Original+1.0 sein
        for c in result:
            assert c["_rrf_score"] >= 0
            assert c["_rrf_score"] <= 1.5

    @pytest.mark.asyncio
    async def test_length_mismatch_falls_back_to_target_k(self, test_settings):
        """H-05/NEW-007: bei Mismatch chunks[:target_k] statt voller Liste."""
        from app.services.rag_service import _llm_rerank
        chunks = [{"doc_id": str(i), "text": f"chunk {i}"} for i in range(10)]
        with patch("app.services.rag_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = '{"scores": [9, 8, 7]}'  # nur 3 statt 10
            result = await _llm_rerank(chunks, "frage", test_settings, target_k=5)
        assert len(result) == 5  # NICHT 10
        assert result == chunks[:5]

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_target_k(self, test_settings):
        from app.services.rag_service import _llm_rerank
        chunks = [{"doc_id": str(i), "text": f"chunk {i}"} for i in range(10)]
        with patch("app.services.rag_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = None  # LLM down
            result = await _llm_rerank(chunks, "frage", test_settings, target_k=4)
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_gracefully(self, test_settings):
        from app.services.rag_service import _llm_rerank
        chunks = [{"doc_id": str(i), "text": f"chunk {i}"} for i in range(8)]
        with patch("app.services.rag_service.call_llm", new_callable=AsyncMock) as m:
            m.return_value = "nicht json"  # JSONDecodeError
            result = await _llm_rerank(chunks, "frage", test_settings, target_k=3)
        assert len(result) == 3
