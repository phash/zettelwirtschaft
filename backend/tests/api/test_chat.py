"""Tests fuer die Chat-API."""

import pytest
from unittest.mock import patch, AsyncMock


class TestChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_success(self, client):
        mock_result = {
            "answer": "Die Versicherung kostet 200 EUR.",
            "sources": [
                {
                    "document_id": "doc-1",
                    "title": "Haftpflichtversicherung",
                    "document_type": "VERSICHERUNGSPOLICE",
                    "document_date": "2024-01-01",
                    "issuer": "Allianz",
                }
            ],
            "chunks_found": 3,
        }

        with patch("app.api.chat.ask_question", new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result
            resp = await client.post("/api/chat", json={"question": "Was kostet die Versicherung?"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["answer"] == "Die Versicherung kostet 200 EUR."
            assert len(data["sources"]) == 1
            assert data["sources"][0]["document_id"] == "doc-1"
            assert data["chunks_found"] == 3

    @pytest.mark.asyncio
    async def test_chat_with_scope_filter(self, client):
        mock_result = {
            "answer": "Antwort",
            "sources": [],
            "chunks_found": 0,
        }

        with patch("app.api.chat.ask_question", new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result
            resp = await client.post("/api/chat", json={
                "question": "Test?",
                "filing_scope_id": "scope-123",
            })
            assert resp.status_code == 200
            # Scope-ID wurde an ask_question uebergeben
            mock_ask.assert_called_once()
            call_kwargs = mock_ask.call_args
            assert call_kwargs.kwargs.get("filing_scope_id") == "scope-123"

    @pytest.mark.asyncio
    async def test_chat_empty_question(self, client):
        resp = await client.post("/api/chat", json={"question": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_service_error(self, client):
        with patch("app.api.chat.ask_question", new_callable=AsyncMock) as mock_ask:
            mock_ask.side_effect = Exception("ChromaDB nicht erreichbar")
            resp = await client.post("/api/chat", json={"question": "Test?"})
            assert resp.status_code == 503


class TestChatHistory:
    @pytest.mark.asyncio
    async def test_empty_history(self, client):
        resp = await client.get("/api/chat/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_history_after_chat(self, client):
        mock_result = {
            "answer": "Testantwort",
            "sources": [],
            "chunks_found": 0,
        }

        with patch("app.api.chat.ask_question", new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result
            await client.post("/api/chat", json={"question": "Testfrage"})

        resp = await client.get("/api/chat/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2  # user + assistant
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Testfrage"
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][1]["content"] == "Testantwort"

    @pytest.mark.asyncio
    async def test_clear_history(self, client):
        mock_result = {
            "answer": "Antwort",
            "sources": [],
            "chunks_found": 0,
        }

        with patch("app.api.chat.ask_question", new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result
            await client.post("/api/chat", json={"question": "Frage"})

        # Loeschen
        resp = await client.delete("/api/chat/history")
        assert resp.status_code == 200

        # Pruefen dass leer
        resp = await client.get("/api/chat/history")
        data = resp.json()
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_history_pagination(self, client):
        mock_result = {
            "answer": "A",
            "sources": [],
            "chunks_found": 0,
        }

        with patch("app.api.chat.ask_question", new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result
            # 3 Frage-Antwort-Paare
            for i in range(3):
                await client.post("/api/chat", json={"question": f"Frage {i}"})

        resp = await client.get("/api/chat/history", params={"limit": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6  # 3 Fragen + 3 Antworten
        assert len(data["messages"]) == 2
