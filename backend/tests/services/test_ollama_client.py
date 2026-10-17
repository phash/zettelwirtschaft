"""Tests fuer den gemeinsamen Ollama-Client (Modell-Checks, Pull, Health-Probe)."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import ollama_client
from app.services.ollama_client import (
    ensure_configured_models,
    model_installed,
    normalize_model_name,
    probe_ollama,
)


@pytest.fixture(autouse=True)
def _reset_state():
    ollama_client.reset_capabilities_cache()
    ollama_client.reset_last_outcomes()
    yield
    ollama_client.reset_capabilities_cache()
    ollama_client.reset_last_outcomes()


def _fake_handler(installed: list[str], pull_lines: dict[str, list[dict]] | None = None, calls: list | None = None):
    """Request-Handler, der /api/tags und /api/pull wie Ollama beantwortet."""
    installed = list(installed)
    pull_lines = pull_lines or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n, "model": n} for n in installed]})
        if request.url.path == "/api/pull":
            name = json.loads(request.content)["model"]
            lines = pull_lines.get(name, [{"status": "success"}])
            if lines and lines[-1].get("status") == "success":
                installed.append(normalize_model_name(name))
            body = "\n".join(json.dumps(line) for line in lines) + "\n"
            return httpx.Response(200, content=body.encode())
        return httpx.Response(404, json={"error": "not found"})

    return handler


def _fake_ollama(installed: list[str], pull_lines: dict[str, list[dict]] | None = None, calls: list | None = None):
    """httpx-Client mit MockTransport ueber ``_fake_handler``."""
    return httpx.AsyncClient(transport=httpx.MockTransport(_fake_handler(installed, pull_lines, calls)))


class TestModelNames:
    @pytest.mark.parametrize("name,expected", [
        ("bge-m3", "bge-m3:latest"),
        ("qwen2.5:7b-instruct", "qwen2.5:7b-instruct"),
        ("registry.local:5000/team/model", "registry.local:5000/team/model:latest"),
        ("hf.co/user/repo:Q4_K_M", "hf.co/user/repo:Q4_K_M"),
    ])
    def test_normalize(self, name, expected):
        assert normalize_model_name(name) == expected

    def test_exact_tag_match(self):
        installed = ["qwen2.5:7b-instruct", "bge-m3:latest"]
        assert model_installed("bge-m3", installed)
        assert model_installed("qwen2.5:7b-instruct", installed)
        # Frueher reichte startswith — ein anderes Tag ist ein anderes Modell.
        assert not model_installed("qwen2.5:7b", installed)
        assert not model_installed("qwen2.5", installed)
        assert not model_installed("bge-m3:567m", installed)


class TestProbe:
    async def test_reports_missing_llm_model(self, test_settings):
        test_settings.OLLAMA_MODEL = "gibtsnicht:7b"
        with patch("app.services.ollama_client._get_client", return_value=_fake_ollama(["bge-m3:latest"])):
            probe = await probe_ollama(test_settings)
        assert probe.reachable
        assert probe.missing == ["gibtsnicht:7b"]
        assert probe.message() == "Modell gibtsnicht:7b nicht installiert"

    async def test_all_present(self, test_settings):
        client = _fake_ollama(["qwen2.5:7b-instruct", "bge-m3:latest"])
        with patch("app.services.ollama_client._get_client", return_value=client):
            probe = await probe_ollama(test_settings)
        assert probe.ok and probe.message() is None

    async def test_unreachable(self, test_settings):
        def handler(request):
            raise httpx.ConnectError("refused")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.ollama_client._get_client", return_value=client):
            probe = await probe_ollama(test_settings)
        assert not probe.reachable
        assert "nicht erreichbar" in probe.message()


class TestEnsureConfiguredModels:
    async def test_pulls_missing_llm_and_embedding_model(self, test_settings):
        """Startup-Auto-Pull deckt OLLAMA_MODEL UND EMBEDDING_MODEL ab (Embedding zuerst)."""
        calls: list = []
        client = _fake_ollama([], calls=calls)
        with patch("app.services.ollama_client._get_client", return_value=client):
            results = await ensure_configured_models(test_settings)

        assert results == {"bge-m3": True, "qwen2.5:7b-instruct": True}
        pulled = [json.loads(body)["model"] for method, path, body in calls if path == "/api/pull"]
        assert pulled == ["bge-m3", "qwen2.5:7b-instruct"]

    async def test_present_models_not_pulled(self, test_settings):
        calls: list = []
        client = _fake_ollama(["qwen2.5:7b-instruct", "bge-m3:latest"], calls=calls)
        with patch("app.services.ollama_client._get_client", return_value=client):
            results = await ensure_configured_models(test_settings)
        assert all(results.values())
        assert not [c for c in calls if c[1] == "/api/pull"]

    async def test_pull_error_reported(self, test_settings):
        client = _fake_ollama(
            ["bge-m3:latest"],
            pull_lines={"qwen2.5:7b-instruct": [
                {"status": "pulling manifest"},
                {"error": "pull model manifest: file does not exist"},
            ]},
        )
        with patch("app.services.ollama_client._get_client", return_value=client):
            results = await ensure_configured_models(test_settings)
        assert results == {"bge-m3": True, "qwen2.5:7b-instruct": False}

    async def test_pull_progress_streamed(self, test_settings, caplog):
        client = _fake_ollama(
            ["bge-m3:latest"],
            pull_lines={"qwen2.5:7b-instruct": [
                {"status": "pulling abc", "total": 100, "completed": 0},
                {"status": "pulling abc", "total": 100, "completed": 50},
                {"status": "success"},
            ]},
        )
        with patch("app.services.ollama_client._get_client", return_value=client):
            with caplog.at_level("INFO", logger="zettelwirtschaft.ollama"):
                results = await ensure_configured_models(test_settings)
        assert results["qwen2.5:7b-instruct"] is True
        assert any("50%" in r.getMessage() for r in caplog.records)

    async def test_unreachable_ollama_returns_false(self, test_settings):
        def handler(request):
            raise httpx.ConnectError("refused")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.ollama_client._get_client", return_value=client):
            results = await ensure_configured_models(test_settings, wait_seconds=0)
        assert results == {"bge-m3": False, "qwen2.5:7b-instruct": False}

    async def test_waits_for_late_ollama_then_pulls(self, test_settings):
        """Native-Boot: Ollama startet nach dem Backend-Dienst — der Auto-Pull
        wartet darauf, statt einmalig "nicht erreichbar" zu melden."""
        ready = _fake_handler([])
        attempts = {"tags": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                attempts["tags"] += 1
                if attempts["tags"] <= 2:
                    raise httpx.ConnectError("refused")
            return ready(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sleep = AsyncMock()
        with patch("app.services.ollama_client._get_client", return_value=client), \
             patch("app.services.ollama_client.asyncio.sleep", sleep):
            results = await ensure_configured_models(test_settings)
        assert results == {"bge-m3": True, "qwen2.5:7b-instruct": True}
        assert sleep.await_count == 2
