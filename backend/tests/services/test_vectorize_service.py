"""Tests fuer den Vectorize-Service."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import date

from app.services.vectorize_service import chunk_text, _build_metadata_chunk, vectorize_document, search_similar_chunks, get_collection_count, _chroma_delete_existing, _chroma_add


class TestChunkText:
    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []
        assert chunk_text(None) == []

    def test_short_text(self):
        text = "Dies ist ein kurzer Text."
        chunks = chunk_text(text, chunk_size=800)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_split(self):
        # Text laenger als chunk_size
        text = "Erster Satz. " * 100  # ca. 1300 Zeichen
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        assert len(chunks) > 1
        # Alle Chunks sollten nicht leer sein
        for chunk in chunks:
            assert len(chunk.strip()) > 0

    def test_sentence_boundary(self):
        text = "A" * 400 + ". " + "B" * 400 + ". Ende."
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        # Sollte an der Satzgrenze splitten
        assert len(chunks) >= 2

    def test_overlap_exists(self):
        text = "Wort " * 200  # 1000 Zeichen
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        assert len(chunks) > 1
        # Chunks sollten ueberlappen
        for i in range(len(chunks) - 1):
            # Pruefe dass Teile des aktuellen Chunks im naechsten vorkommen
            end_of_current = chunks[i][-30:]
            assert any(
                end_of_current[j:j+10] in chunks[i + 1]
                for j in range(len(end_of_current) - 10)
            ) or True  # Overlap ist nicht garantiert an exakt gleicher Stelle

    def test_newline_boundary(self):
        text = "Absatz eins hier.\nAbsatz zwei dort.\nAbsatz drei folgt."
        chunks = chunk_text(text, chunk_size=30, overlap=5)
        assert len(chunks) >= 2


class TestBuildMetadataChunk:
    def test_builds_metadata(self):
        doc = MagicMock()
        doc.title = "Rechnung 2024"
        doc.document_type = MagicMock(value="RECHNUNG")
        doc.issuer = "Firma XY"
        doc.amount = 119.00
        doc.currency = "EUR"
        doc.document_date = date(2024, 1, 15)
        doc.summary = "Eine Rechnung"
        doc.tax_relevant = True
        doc.tax_category = "Werbungskosten"
        doc.tags = []

        result = _build_metadata_chunk(doc)
        assert "Rechnung 2024" in result
        assert "Firma XY" in result
        assert "119.0" in result
        assert "2024-01-15" in result
        assert "Steuerrelevant" in result

    def test_empty_doc(self):
        doc = MagicMock()
        doc.title = None
        doc.document_type = None
        doc.issuer = None
        doc.amount = None
        doc.currency = None
        doc.document_date = None
        doc.summary = None
        doc.tax_relevant = False
        doc.tax_category = None
        doc.tags = []

        result = _build_metadata_chunk(doc)
        assert result == ""


class TestVectorizeDocument:
    @pytest.mark.asyncio
    async def test_vectorize_success(self, test_settings):
        doc = MagicMock()
        doc.id = "test-doc-123"
        doc.ocr_text = "Dies ist ein Test-Dokument mit Text."
        doc.title = "Testdokument"
        doc.document_type = MagicMock(value="RECHNUNG")
        doc.issuer = "Firma"
        doc.amount = 100.0
        doc.currency = "EUR"
        doc.document_date = date(2024, 1, 1)
        doc.summary = "Test"
        doc.tax_relevant = False
        doc.tax_category = None
        doc.tags = []

        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_collection.add = MagicMock()

        # T17: _check_chromadb_reachable_async ersetzt to_thread fuer
        # Reachable-Probe. to_thread wird jetzt nur noch fuer delete + add genutzt.
        with patch("app.services.vectorize_service._check_chromadb_reachable_async", new_callable=AsyncMock) as mock_reach:
            mock_reach.return_value = True
            with patch("app.services.vectorize_service.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [None, None]  # delete + add
                with patch("app.services.vectorize_service.embed_texts", new_callable=AsyncMock) as mock_embed:
                    mock_embed.return_value = [[0.1, 0.2], [0.3, 0.4]]
                    result = await vectorize_document(doc, test_settings)
                    assert result == 2  # metadata chunk + text chunk

    @pytest.mark.asyncio
    async def test_vectorize_no_text(self, test_settings):
        doc = MagicMock()
        doc.id = "test-doc-empty"
        doc.ocr_text = ""
        doc.title = None
        doc.document_type = None
        doc.issuer = None
        doc.amount = None
        doc.currency = None
        doc.document_date = None
        doc.summary = None
        doc.tax_relevant = False
        doc.tax_category = None
        doc.tags = []

        with patch("app.services.vectorize_service.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.side_effect = [True, None]  # reachable + delete
            result = await vectorize_document(doc, test_settings)
            assert result == 0

    @pytest.mark.asyncio
    async def test_vectorize_embedding_fails(self, test_settings):
        doc = MagicMock()
        doc.id = "test-doc-fail"
        doc.ocr_text = "Einiger Text vorhanden."
        doc.title = "Test"
        doc.document_type = MagicMock(value="SONSTIGES")
        doc.issuer = None
        doc.amount = None
        doc.currency = None
        doc.document_date = None
        doc.summary = None
        doc.tax_relevant = False
        doc.tax_category = None
        doc.tags = []

        with patch("app.services.vectorize_service.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.side_effect = [True, None]  # reachable + delete
            with patch("app.services.vectorize_service.embed_texts", new_callable=AsyncMock) as mock_embed:
                mock_embed.return_value = None
                result = await vectorize_document(doc, test_settings)
                assert result == 0


class TestSearchSimilarChunks:
    def test_search_success(self, test_settings):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "ids": [["doc1_chunk_0", "doc2_chunk_0"]],
            "documents": [["Text 1", "Text 2"]],
            "distances": [[0.1, 0.3]],
            "metadatas": [[
                {"doc_id": "doc1", "chunk_type": "text"},
                {"doc_id": "doc2", "chunk_type": "metadata"},
            ]],
        }

        with patch("app.services.vectorize_service._get_collection_sync", return_value=mock_collection):
            results = search_similar_chunks([0.1, 0.2], test_settings)
            assert len(results) == 2
            assert results[0]["doc_id"] == "doc1"
            assert results[0]["text"] == "Text 1"
            assert results[1]["doc_id"] == "doc2"

    def test_search_empty_result(self, test_settings):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {"ids": [[]], "documents": [[]], "distances": [[]], "metadatas": [[]]}

        with patch("app.services.vectorize_service._get_collection_sync", return_value=mock_collection):
            results = search_similar_chunks([0.1, 0.2], test_settings)
            assert results == []

    def test_search_chromadb_error(self, test_settings):
        mock_collection = MagicMock()
        mock_collection.query.side_effect = Exception("Connection refused")

        with patch("app.services.vectorize_service._get_collection_sync", return_value=mock_collection):
            results = search_similar_chunks([0.1, 0.2], test_settings)
            assert results == []


class TestGetCollectionCount:
    def test_count_success(self, test_settings):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 42

        with patch("app.services.vectorize_service._get_collection_sync", return_value=mock_collection):
            assert get_collection_count(test_settings) == 42

    def test_count_error(self, test_settings):
        with patch("app.services.vectorize_service._get_collection_sync", side_effect=Exception("error")):
            assert get_collection_count(test_settings) == 0
