"""AVAIL-03: /api/health (ohne PIN, ohne Rate-Limit) darf bei langsamer
Ollama-Probe weder Ollama noch den DB-Pool fluten.

- Cache-Zeitstempel nach der Probe: eine Probe >= HEALTH_TTL_SECONDS liefert
  danach trotzdem Cache-Treffer.
- Single-Flight: gleichzeitige Aufrufer teilen sich eine Probe.
- Der Health-Handler gibt seine DB-Verbindung vor dem Warten auf Ollama zurueck.
"""

import asyncio
from unittest.mock import patch

import pytest

from app.services import ollama_client


def _probe_unreachable() -> ollama_client.OllamaProbe:
    return ollama_client.OllamaProbe(reachable=False, error="Ollama nicht erreichbar")


@pytest.fixture
def slow_probe(monkeypatch):
    calls: list[int] = []

    async def fake_probe(settings):
        calls.append(1)
        await asyncio.sleep(0.3)
        return _probe_unreachable()

    monkeypatch.setattr(ollama_client, "probe_ollama", fake_probe)
    return calls


async def test_slow_probe_is_cached(slow_probe, test_settings, monkeypatch):
    # Probe dauert laenger als die TTL -> frueher sofort abgelaufen.
    monkeypatch.setattr(ollama_client, "HEALTH_TTL_SECONDS", 0.2)
    await ollama_client.ollama_health(test_settings)
    await ollama_client.ollama_health(test_settings)
    assert len(slow_probe) == 1


async def test_concurrent_callers_share_one_probe(slow_probe, test_settings):
    results = await asyncio.gather(
        *(ollama_client.ollama_health(test_settings) for _ in range(20))
    )
    assert len(slow_probe) == 1
    assert all(r is results[0] for r in results)


async def test_cancelled_caller_does_not_cancel_shared_probe(slow_probe, test_settings):
    first = asyncio.ensure_future(ollama_client.ollama_health(test_settings))
    await asyncio.sleep(0.05)
    second = asyncio.ensure_future(ollama_client.ollama_health(test_settings))
    first.cancel()
    health = await second
    assert health.status == "warning"
    assert len(slow_probe) == 1


async def test_health_releases_db_connection_before_ollama(
    slow_probe, test_settings, test_engine, test_session_factory
):
    """Waehrend die (langsame) Probe laeuft, haelt der Handler keine
    ausgecheckte Pool-Verbindung mehr."""
    from tests.conftest import make_test_client

    pool = test_engine.sync_engine.pool
    seen: list[int] = []

    async def probe_and_look(settings):
        seen.append(pool.checkedout())
        slow_probe.append(1)
        await asyncio.sleep(0.05)
        return _probe_unreachable()

    with patch.object(ollama_client, "probe_ollama", probe_and_look):
        async with make_test_client(test_settings, test_session_factory) as client:
            resp = await client.get("/api/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["database"]["status"] == "ok"
    assert seen == [0], f"Pool-Verbindungen waehrend Ollama-Probe: {seen}"
