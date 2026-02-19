import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings
from app.services.llm_service import call_llm, check_ollama_available, load_prompt_template


class TestLoadPromptTemplate:
    def test_load_existing_template(self):
        """Vorhandenes Template wird geladen."""
        content = load_prompt_template("analyze_document.txt")
        assert "{ocr_text}" in content
        assert "document_type" in content

    def test_load_nonexistent_template(self):
        """Fehlendes Template wirft FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_prompt_template("nonexistent.txt")

    def test_all_templates_exist(self):
        """Alle erwarteten Templates existieren."""
        templates = [
            "analyze_document.txt",
            "classify_document.txt",
            "extract_metadata.txt",
            "assess_tax_relevance.txt",
            "extract_warranty_info.txt",
        ]
        for name in templates:
            content = load_prompt_template(name)
            assert content, f"Template {name} ist leer"
            assert "{ocr_text}" in content, f"Template {name} hat keinen {{ocr_text}} Platzhalter"


def _make_response(status_code: int, json_data: dict | None = None) -> httpx.Response:
    """Erstellt eine httpx.Response mit gesetztem Request."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
    )
    return resp


class TestCallLlm:
    async def test_successful_call(self, test_settings: Settings, mock_ollama_response: dict):
        """Erfolgreicher LLM-Aufruf gibt Antwort zurueck."""
        mock_response = _make_response(200, mock_ollama_response)

        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await call_llm("Analysiere dieses Dokument", test_settings)

            assert result is not None
            data = json.loads(result)
            assert data["document_type"] == "RECHNUNG"
            assert data["confidence"] == 0.92

    async def test_connection_error_retries(self, test_settings: Settings):
        """Bei ConnectError wird wiederholt versucht."""
        test_settings.OLLAMA_MAX_RETRIES = 1

        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with patch("app.services.llm_service.asyncio.sleep", new_callable=AsyncMock):
                result = await call_llm("Test", test_settings)

            assert result is None
            # 1 initial + 1 retry = 2 Aufrufe
            assert mock_client.post.call_count == 2

    async def test_timeout_retries(self, test_settings: Settings):
        """Bei Timeout wird wiederholt versucht."""
        test_settings.OLLAMA_MAX_RETRIES = 1

        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with patch("app.services.llm_service.asyncio.sleep", new_callable=AsyncMock):
                result = await call_llm("Test", test_settings)

            assert result is None
            assert mock_client.post.call_count == 2

    async def test_http_error_no_retry(self, test_settings: Settings):
        """Bei HTTP-Fehler (z.B. 500) wird nicht wiederholt."""
        mock_response = _make_response(500)

        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await call_llm("Test", test_settings)

            assert result is None

    async def test_empty_response(self, test_settings: Settings):
        """Leere LLM-Antwort gibt None zurueck."""
        mock_response = _make_response(200, {"message": {"role": "assistant", "content": ""}})

        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await call_llm("Test", test_settings)

            assert result is None

    async def test_system_prompt_included(self, test_settings: Settings, mock_ollama_response: dict):
        """System-Prompt wird in Messages aufgenommen."""
        mock_response = _make_response(200, mock_ollama_response)

        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await call_llm("User prompt", test_settings, system_prompt="System prompt")

            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            messages = payload["messages"]
            assert len(messages) == 2
            assert messages[0]["role"] == "system"
            assert messages[0]["content"] == "System prompt"


class TestCheckOllamaAvailable:
    async def test_ollama_available(self, test_settings: Settings):
        """Prueft positive Ollama-Erreichbarkeit."""
        mock_response = httpx.Response(
            status_code=200,
            json={"models": []},
            request=httpx.Request("GET", "http://localhost:11434/api/tags"),
        )

        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await check_ollama_available(test_settings)
            assert result is True

    async def test_ollama_unavailable(self, test_settings: Settings):
        """Prueft negative Ollama-Erreichbarkeit."""
        with patch("app.services.llm_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await check_ollama_available(test_settings)
            assert result is False
