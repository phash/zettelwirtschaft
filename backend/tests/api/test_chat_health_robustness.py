"""Regressionstests: Chat speichert nur erfolgreiche Antworten, validiert den
Ablagebereich vorab; /api/health meldet fehlende Modelle und Ausfaelle korrekt."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import ollama_client
from app.services.ollama_client import OllamaError, OllamaErrorKind, OllamaProbe
from app.services.rag_service import RagServiceUnavailable


@pytest.fixture(autouse=True)
def _reset_health_cache():
    ollama_client.reset_health_cache()
    ollama_client.reset_last_outcomes()
    yield
    ollama_client.reset_health_cache()
    ollama_client.reset_last_outcomes()


async def _history_total(client) -> int:
    return (await client.get("/api/chat/history")).json()["total"]


class TestChatPersistence:
    async def test_unknown_scope_rejected_before_llm(self, client):
        with patch("app.api.chat.ask_question", new_callable=AsyncMock) as ask:
            resp = await client.post("/api/chat", json={"question": "Test?", "filing_scope_id": "gibt-es-nicht"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Ablagebereich nicht gefunden"
        ask.assert_not_awaited()
        assert await _history_total(client) == 0

    @pytest.mark.parametrize("message", [
        "Der KI-Dienst (Ollama) ist nicht erreichbar. Bitte versuchen Sie es später erneut.",
        "Der KI-Assistent konnte keine Antwort erzeugen. Bitte versuchen Sie es später erneut.",
    ])
    async def test_service_unavailable_is_503_and_not_saved(self, client, message):
        with patch("app.api.chat.ask_question", AsyncMock(side_effect=RagServiceUnavailable(message))):
            resp = await client.post("/api/chat", json={"question": "Test?"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == message
        assert await _history_total(client) == 0

    async def test_embedding_outage_end_to_end(self, client):
        """Echter RAG-Pfad: Embedding-Ausfall -> 503 mit Grund, kein Verlaufseintrag."""
        failing = AsyncMock(side_effect=OllamaError(OllamaErrorKind.MODEL_MISSING, "not found", "bge-m3"))
        with patch("app.services.rag_service.embed_query", failing):
            resp = await client.post("/api/chat", json={"question": "Was kostet die Versicherung?"})
        assert resp.status_code == 503
        assert "bge-m3" in resp.json()["detail"] and "nicht installiert" in resp.json()["detail"]
        assert await _history_total(client) == 0

    async def test_disconnected_client_answer_not_saved(self, client):
        result = {"answer": "A", "sources": [], "chunks_found": 0}
        with patch("app.api.chat.ask_question", AsyncMock(return_value=result)), \
             patch("starlette.requests.Request.is_disconnected", AsyncMock(return_value=True)):
            await client.post("/api/chat", json={"question": "Test?"})
        assert await _history_total(client) == 0

    async def test_sources_without_title_in_history(self, client):
        result = {"answer": "A", "sources": [{"document_id": "d", "title": None}], "chunks_found": 1}
        with patch("app.api.chat.ask_question", AsyncMock(return_value=result)):
            assert (await client.post("/api/chat", json={"question": "Q"})).status_code == 200
        history = (await client.get("/api/chat/history")).json()
        assert history["messages"][1]["sources"][0]["document_id"] == "d"

    async def test_clear_history_message_has_umlaut(self, client):
        assert (await client.delete("/api/chat/history")).json()["message"] == "Chatverlauf gelöscht"


class TestHealth:
    async def test_missing_model_reported_as_warning(self, client):
        probe = OllamaProbe(reachable=True, models=["bge-m3:latest"], missing=["qwen2.5:7b-instruct"])
        with patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=probe)):
            resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "warning"
        assert data["ollama"] == {"status": "warning", "message": "Modell qwen2.5:7b-instruct nicht installiert"}

    async def test_failed_last_llm_call_reported(self, client, test_settings):
        """Ollama antwortet auf /api/tags, aber jeder Chat-Aufruf scheitert (500)."""
        ollama_client._record_outcome(
            "llm", test_settings.OLLAMA_MODEL, OllamaError(OllamaErrorKind.SERVER_ERROR, "HTTP 500"),
        )
        with patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=OllamaProbe(reachable=True, models=[]))):
            data = (await client.get("/api/health")).json()
        assert data["ollama"]["status"] == "warning"
        assert "Letzter KI-Aufruf fehlgeschlagen" in data["ollama"]["message"]

    async def test_pulling_model_reported(self, client):
        probe = OllamaProbe(reachable=True, models=[], missing=["qwen2.5:7b-instruct"])
        ollama_client._pulling.add("qwen2.5:7b-instruct")
        try:
            with patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=probe)):
                data = (await client.get("/api/health")).json()
        finally:
            ollama_client._pulling.discard("qwen2.5:7b-instruct")
        assert data["ollama"]["message"] == "Modell qwen2.5:7b-instruct wird heruntergeladen"

    async def test_all_ok(self, client):
        with patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=OllamaProbe(reachable=True, models=["x"]))):
            resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["ollama"] == {"status": "ok", "message": None}

    async def test_storage_error_is_503_without_path_leak(self, client):
        with patch("app.api.health.shutil.disk_usage", side_effect=OSError("[Errno 2] /app/data/archive fehlt")), \
             patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=OllamaProbe(reachable=True))):
            resp = await client.get("/api/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "error"
        assert data["storage"] == {"status": "error", "message": "Speicherort nicht verfügbar"}
        assert "/app" not in resp.text

    async def test_database_error_is_503(self, client):
        from sqlalchemy.ext.asyncio import AsyncSession

        original = AsyncSession.execute

        async def failing_execute(self, statement, *args, **kwargs):
            if "SELECT 1" in str(statement):
                raise RuntimeError("db kaputt")
            return await original(self, statement, *args, **kwargs)

        with patch.object(AsyncSession, "execute", failing_execute), \
             patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=OllamaProbe(reachable=True))):
            resp = await client.get("/api/health")
        assert resp.status_code == 503
        assert resp.json()["database"] == {"status": "error", "message": "Datenbankfehler"}
