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
        with patch("app.services.ollama_client._get_client", return_value=_fake_ollama(["qwen3-embedding:4b"])):
            probe = await probe_ollama(test_settings)
        assert probe.reachable
        assert probe.missing == ["gibtsnicht:7b"]
        assert probe.message() == "Modell gibtsnicht:7b nicht installiert"

    async def test_all_present(self, test_settings):
        client = _fake_ollama(["gemma4:e2b-it-qat", "qwen3-embedding:4b"])
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

        assert results == {"qwen3-embedding:4b": True, "gemma4:e2b-it-qat": True}
        pulled = [json.loads(body)["model"] for method, path, body in calls if path == "/api/pull"]
        assert pulled == ["qwen3-embedding:4b", "gemma4:e2b-it-qat"]

    async def test_present_models_not_pulled(self, test_settings):
        calls: list = []
        client = _fake_ollama(["gemma4:e2b-it-qat", "qwen3-embedding:4b"], calls=calls)
        with patch("app.services.ollama_client._get_client", return_value=client):
            results = await ensure_configured_models(test_settings)
        assert all(results.values())
        assert not [c for c in calls if c[1] == "/api/pull"]

    async def test_pull_error_reported(self, test_settings):
        client = _fake_ollama(
            ["qwen3-embedding:4b"],
            pull_lines={"gemma4:e2b-it-qat": [
                {"status": "pulling manifest"},
                {"error": "pull model manifest: file does not exist"},
            ]},
        )
        with patch("app.services.ollama_client._get_client", return_value=client):
            results = await ensure_configured_models(test_settings)
        assert results == {"qwen3-embedding:4b": True, "gemma4:e2b-it-qat": False}

    async def test_pull_progress_streamed(self, test_settings, caplog):
        client = _fake_ollama(
            ["qwen3-embedding:4b"],
            pull_lines={"gemma4:e2b-it-qat": [
                {"status": "pulling abc", "total": 100, "completed": 0},
                {"status": "pulling abc", "total": 100, "completed": 50},
                {"status": "success"},
            ]},
        )
        with patch("app.services.ollama_client._get_client", return_value=client):
            with caplog.at_level("INFO", logger="zettelwirtschaft.ollama"):
                results = await ensure_configured_models(test_settings)
        assert results["gemma4:e2b-it-qat"] is True
        assert any("50%" in r.getMessage() for r in caplog.records)

    async def test_unreachable_ollama_returns_false(self, test_settings):
        def handler(request):
            raise httpx.ConnectError("refused")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.ollama_client._get_client", return_value=client):
            results = await ensure_configured_models(test_settings, wait_seconds=0)
        assert results == {"qwen3-embedding:4b": False, "gemma4:e2b-it-qat": False}

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
        assert results == {"qwen3-embedding:4b": True, "gemma4:e2b-it-qat": True}
        assert sleep.await_count == 2


class TestOllamaVersion:
    @pytest.mark.parametrize("raw,expected", [
        ("0.40.5", (0, 40, 5)),
        ("0.40.6-rc0", (0, 40, 6)),
        ("v0.30.3", (0, 30, 3)),
        ("0.12", (0, 12, 0)),
        ("0.0.0", None),  # Entwickler-Build
        ("", None),
        (None, None),
        ("unbekannt", None),
    ])
    def test_parse_version(self, raw, expected):
        assert ollama_client.parse_version(raw) == expected

    def test_version_warning(self):
        assert ollama_client.version_warning("0.12.6", "0.30.3") == (
            "Ollama 0.12.6 ist älter als 0.30.3 — bitte aktualisieren"
        )
        assert ollama_client.version_warning("0.30.3", "0.30.3") is None
        assert ollama_client.version_warning("0.40.5", "0.30.3") is None
        assert ollama_client.version_warning("0.0.0", "0.30.3") is None
        assert ollama_client.version_warning(None, "0.30.3") is None

    def test_minimum_not_above_tested_version(self):
        assert ollama_client.parse_version(ollama_client.OLLAMA_MIN_VERSION) <= ollama_client.parse_version(
            ollama_client.OLLAMA_TESTED_VERSION
        )

    async def test_get_version(self, test_settings):
        def handler(request):
            assert request.url.path == "/api/version"
            return httpx.Response(200, json={"version": "0.40.5"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.ollama_client._get_client", return_value=client):
            assert await ollama_client.get_ollama_version(test_settings) == "0.40.5"

    async def test_get_version_unavailable(self, test_settings):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="404 page not found")))
        with patch("app.services.ollama_client._get_client", return_value=client):
            assert await ollama_client.get_ollama_version(test_settings) is None


def _versioned_ollama(installed: list[str], version: str | None):
    tags = _fake_handler(installed)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            if version is None:
                return httpx.Response(404, text="404 page not found")
            return httpx.Response(200, json={"version": version})
        return tags(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestHealthVersionCheck:
    async def test_old_version_is_warning_not_error(self, test_settings):
        installed = [test_settings.OLLAMA_MODEL, test_settings.EMBEDDING_MODEL]
        with patch("app.services.ollama_client._get_client", return_value=_versioned_ollama(installed, "0.12.6")):
            health = await ollama_client.ollama_health(test_settings)
        assert health.status == "warning"
        assert health.version == "0.12.6"
        assert health.message == f"Ollama 0.12.6 ist älter als {ollama_client.OLLAMA_MIN_VERSION} — bitte aktualisieren"

    async def test_current_version_ok(self, test_settings):
        installed = [test_settings.OLLAMA_MODEL, test_settings.EMBEDDING_MODEL]
        with patch("app.services.ollama_client._get_client", return_value=_versioned_ollama(installed, "0.40.5")):
            health = await ollama_client.ollama_health(test_settings)
        assert health.status == "ok" and health.message is None and health.version == "0.40.5"

    async def test_unknown_version_no_warning(self, test_settings):
        installed = [test_settings.OLLAMA_MODEL, test_settings.EMBEDDING_MODEL]
        with patch("app.services.ollama_client._get_client", return_value=_versioned_ollama(installed, None)):
            health = await ollama_client.ollama_health(test_settings)
        assert health.status == "ok" and health.version is None

    async def test_old_version_combined_with_missing_model(self, test_settings):
        with patch("app.services.ollama_client._get_client",
                   return_value=_versioned_ollama([test_settings.EMBEDDING_MODEL], "0.20.0")):
            health = await ollama_client.ollama_health(test_settings)
        assert health.status == "warning"
        assert health.message.startswith("Ollama 0.20.0 ist älter als")
        assert f"Modell {test_settings.OLLAMA_MODEL} nicht installiert" in health.message
