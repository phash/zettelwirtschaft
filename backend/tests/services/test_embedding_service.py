"""Tests fuer den Embedding-Service."""

import pytest
import httpx
from unittest.mock import patch, AsyncMock

from app.services.embedding_service import (
    EMBED_BATCH_SIZE,
    QWEN3_EMBEDDING_QUERY_PREFIX,
    embed_batch,
    embed_query,
    embed_text,
    embed_texts,
    format_query,
    query_prefix,
)
from app.services.ollama_client import OllamaError, OllamaErrorKind


def _make_response(status_code, json_data, method="POST", url="http://test"):
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request(method, url),
    )


def _client(*side_effect) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(side_effect=list(side_effect))
    return client


class TestEmbedText:
    @pytest.mark.asyncio
    async def test_embed_text_success(self, test_settings):
        mock_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        client = _client(_make_response(200, {"embeddings": [mock_embedding]}))

        with patch("app.services.ollama_client._get_client", return_value=client):
            result = await embed_text("Test text", test_settings)
        assert result == mock_embedding

    @pytest.mark.asyncio
    async def test_embed_text_returns_none_on_error(self, test_settings):
        test_settings.OLLAMA_MAX_RETRIES = 0
        client = _client(httpx.ConnectError("Connection refused"))

        with patch("app.services.ollama_client._get_client", return_value=client):
            result = await embed_text("Test", test_settings)
        assert result is None


class TestEmbedTexts:
    @pytest.mark.asyncio
    async def test_embed_texts_batch(self, test_settings):
        embeddings = [[0.1, 0.2], [0.3, 0.4]]
        client = _client(_make_response(200, {"embeddings": embeddings}))

        with patch("app.services.ollama_client._get_client", return_value=client):
            result = await embed_texts(["Text 1", "Text 2"], test_settings)
        assert result == embeddings

    @pytest.mark.asyncio
    async def test_payload_has_keep_alive_and_context(self, test_settings):
        test_settings.OLLAMA_KEEP_ALIVE = "10m"
        test_settings.OLLAMA_NUM_CTX = 4096
        client = _client(_make_response(200, {"embeddings": [[0.1]]}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            await embed_texts(["x"], test_settings)
        payload = client.post.call_args.kwargs["json"]
        assert payload["keep_alive"] == "10m"
        assert payload["options"]["num_ctx"] == 4096

    @pytest.mark.asyncio
    async def test_embed_texts_empty_list(self, test_settings):
        result = await embed_texts([], test_settings)
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_texts_empty_response(self, test_settings):
        client = _client(_make_response(200, {"embeddings": []}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            result = await embed_texts(["Test"], test_settings)
        assert result is None

    @pytest.mark.asyncio
    async def test_connect_error_retry(self, test_settings):
        test_settings.OLLAMA_MAX_RETRIES = 1
        embeddings = [[0.1, 0.2]]
        client = _client(httpx.ConnectError("refused"), _make_response(200, {"embeddings": embeddings}))

        with patch("app.services.ollama_client._get_client", return_value=client), \
             patch("app.services.ollama_client.asyncio.sleep", new_callable=AsyncMock):
            result = await embed_texts(["Test"], test_settings)
        assert result == embeddings

    @pytest.mark.asyncio
    async def test_server_error_is_retried(self, test_settings):
        """5xx wird wie beim LLM wiederholt (shared Retry-Helper)."""
        test_settings.OLLAMA_MAX_RETRIES = 1
        embeddings = [[0.1, 0.2]]
        client = _client(
            _make_response(500, {"error": "internal"}),
            _make_response(200, {"embeddings": embeddings}),
        )
        with patch("app.services.ollama_client._get_client", return_value=client), \
             patch("app.services.ollama_client.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await embed_texts(["Test"], test_settings)
        assert result == embeddings
        assert client.post.call_count == 2
        sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persistent_server_error_gives_none(self, test_settings):
        test_settings.OLLAMA_MAX_RETRIES = 1
        client = _client(_make_response(500, {"error": "internal"}), _make_response(500, {"error": "internal"}))
        with patch("app.services.ollama_client._get_client", return_value=client), \
             patch("app.services.ollama_client.asyncio.sleep", new_callable=AsyncMock):
            result = await embed_texts(["Test"], test_settings)
        assert result is None
        assert client.post.call_count == 2


class TestEmbedBatch:
    @pytest.mark.asyncio
    async def test_large_input_is_split_into_batches(self, test_settings):
        texts = [f"Text {i}" for i in range(EMBED_BATCH_SIZE + 5)]

        async def fake_post(url, json, timeout):
            return _make_response(200, {"embeddings": [[float(i)] for i, _ in enumerate(json["input"])]})

        client = AsyncMock()
        client.post = AsyncMock(side_effect=fake_post)
        with patch("app.services.ollama_client._get_client", return_value=client):
            vectors = await embed_batch(texts, test_settings)

        assert len(vectors) == len(texts)
        sizes = [len(c.kwargs["json"]["input"]) for c in client.post.call_args_list]
        assert sizes == [EMBED_BATCH_SIZE, 5]

    @pytest.mark.asyncio
    async def test_count_mismatch_raises(self, test_settings):
        client = _client(_make_response(200, {"embeddings": [[0.1]]}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            with pytest.raises(OllamaError) as exc:
                await embed_batch(["a", "b"], test_settings)
        assert exc.value.kind == OllamaErrorKind.INVALID_RESPONSE

    @pytest.mark.asyncio
    async def test_missing_model_raises_typed_error(self, test_settings):
        client = _client(_make_response(404, {"error": 'model "bge-m3" not found, try pulling it first'}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            with pytest.raises(OllamaError) as exc:
                await embed_batch(["a"], test_settings)
        assert exc.value.kind == OllamaErrorKind.MODEL_MISSING


class TestQueryPrefix:
    """qwen3-embedding* braucht den Instruct-Prefix NUR fuer Anfragen."""

    @pytest.mark.parametrize("model", [
        "qwen3-embedding:4b",
        "qwen3-embedding:0.6b",
        "qwen3-embedding:8b",
        "qwen3-embedding",
        "Qwen3-Embedding:4B",
        "registry.local:5000/library/qwen3-embedding:4b",
    ])
    def test_qwen3_embedding_family_gets_prefix(self, model):
        assert query_prefix(model) == QWEN3_EMBEDDING_QUERY_PREFIX

    @pytest.mark.parametrize("model", ["bge-m3", "bge-m3:latest", "nomic-embed-text", "embeddinggemma:latest", "qwen3:4b", ""])
    def test_other_models_get_no_prefix(self, model):
        assert query_prefix(model) == ""
        assert format_query("Wie hoch war die Stromrechnung?", model) == "Wie hoch war die Stromrechnung?"

    def test_prefix_text_matches_benchmark(self):
        assert format_query("Wann laeuft die Garantie ab?", "qwen3-embedding:4b") == (
            "Instruct: Given a question about a personal household document, retrieve the "
            "document passages that answer the question\nQuery:Wann laeuft die Garantie ab?"
        )

    @pytest.mark.asyncio
    async def test_embed_query_sends_prefixed_query(self, test_settings):
        test_settings.EMBEDDING_MODEL = "qwen3-embedding:4b"
        client = _client(_make_response(200, {"embeddings": [[0.5, 0.5]]}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            vector = await embed_query("Stromrechnung 2025?", test_settings)
        assert vector == [0.5, 0.5]
        payload = client.post.call_args.kwargs["json"]
        assert payload["model"] == "qwen3-embedding:4b"
        assert payload["input"] == [QWEN3_EMBEDDING_QUERY_PREFIX + "Stromrechnung 2025?"]

    @pytest.mark.asyncio
    async def test_embed_query_without_prefix_for_bge_m3(self, test_settings):
        test_settings.EMBEDDING_MODEL = "bge-m3"
        client = _client(_make_response(200, {"embeddings": [[0.1]]}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            await embed_query("Stromrechnung 2025?", test_settings)
        assert client.post.call_args.kwargs["json"]["input"] == ["Stromrechnung 2025?"]

    @pytest.mark.asyncio
    async def test_documents_are_never_prefixed(self, test_settings):
        """Dokument-Chunks (embed_batch, Vektorisierung) bleiben ohne Prefix."""
        test_settings.EMBEDDING_MODEL = "qwen3-embedding:4b"
        client = _client(_make_response(200, {"embeddings": [[0.1], [0.2]]}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            await embed_batch(["Chunk eins.", "Chunk zwei."], test_settings)
        assert client.post.call_args.kwargs["json"]["input"] == ["Chunk eins.", "Chunk zwei."]

    @pytest.mark.asyncio
    async def test_embed_query_raises_on_failure(self, test_settings):
        client = _client(_make_response(404, {"error": 'model "qwen3-embedding:4b" not found'}))
        with patch("app.services.ollama_client._get_client", return_value=client):
            with pytest.raises(OllamaError) as exc:
                await embed_query("x", test_settings)
        assert exc.value.kind == OllamaErrorKind.MODEL_MISSING
