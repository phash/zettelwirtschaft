"""Tests fuer den Embedding-Service."""

import pytest
import httpx
from unittest.mock import patch, AsyncMock

from app.services.embedding_service import embed_text, embed_texts, ensure_embedding_model


def _make_response(status_code, json_data, method="POST", url="http://test"):
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request(method, url),
    )


class TestEmbedText:
    @pytest.mark.asyncio
    async def test_embed_text_success(self, test_settings):
        mock_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_resp = _make_response(200, {"embeddings": [mock_embedding]})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.embedding_service._get_client", return_value=mock_client):
            result = await embed_text("Test text", test_settings)
            assert result == mock_embedding

    @pytest.mark.asyncio
    async def test_embed_text_returns_none_on_error(self, test_settings):
        # N-01 (Re-Review): nutzt jetzt shared Singleton-Client via _get_client.
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("app.services.embedding_service._get_client", return_value=mock_client):
            test_settings.OLLAMA_MAX_RETRIES = 0
            result = await embed_text("Test", test_settings)
            assert result is None


class TestEmbedTexts:
    @pytest.mark.asyncio
    async def test_embed_texts_batch(self, test_settings):
        embeddings = [[0.1, 0.2], [0.3, 0.4]]
        mock_resp = _make_response(200, {"embeddings": embeddings})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.embedding_service._get_client", return_value=mock_client):
            result = await embed_texts(["Text 1", "Text 2"], test_settings)
            assert result == embeddings
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_embed_texts_empty_list(self, test_settings):
        result = await embed_texts([], test_settings)
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_texts_empty_response(self, test_settings):
        mock_resp = _make_response(200, {"embeddings": []})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.embedding_service._get_client", return_value=mock_client):
            result = await embed_texts(["Test"], test_settings)
            assert result is None

    @pytest.mark.asyncio
    async def test_embed_texts_timeout_retry(self, test_settings):
        test_settings.OLLAMA_MAX_RETRIES = 1
        embeddings = [[0.1, 0.2]]
        mock_resp = _make_response(200, {"embeddings": embeddings})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=[httpx.TimeoutException("timeout"), mock_resp]
        )

        with patch("app.services.embedding_service._get_client", return_value=mock_client):
            with patch("app.services.embedding_service.asyncio.sleep", new_callable=AsyncMock):
                result = await embed_texts(["Test"], test_settings)
                assert result == embeddings

    @pytest.mark.asyncio
    async def test_embed_texts_http_error(self, test_settings):
        mock_resp = _make_response(500, {"error": "internal"})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.embedding_service._get_client", return_value=mock_client):
            result = await embed_texts(["Test"], test_settings)
            assert result is None


class TestEnsureEmbeddingModel:
    @pytest.mark.asyncio
    async def test_model_already_present(self, test_settings):
        # Default-EMBEDDING_MODEL ist bge-m3 (siehe config.py); Mock muss matchen.
        tags_resp = _make_response(200, {
            "models": [{"name": "bge-m3:latest"}]
        }, method="GET")

        with patch("app.services.embedding_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=tags_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await ensure_embedding_model(test_settings)
            assert result is True

    @pytest.mark.asyncio
    async def test_model_pull_success(self, test_settings):
        tags_resp = _make_response(200, {"models": []}, method="GET")
        pull_resp = _make_response(200, {"status": "success"})

        with patch("app.services.embedding_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=tags_resp)
            mock_client.post = AsyncMock(return_value=pull_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await ensure_embedding_model(test_settings)
            assert result is True

    @pytest.mark.asyncio
    async def test_ollama_not_reachable(self, test_settings):
        with patch("app.services.embedding_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await ensure_embedding_model(test_settings)
            assert result is False
