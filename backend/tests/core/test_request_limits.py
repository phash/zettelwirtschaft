"""AVAIL-01: kleine Body-Grenzen fuer Nicht-Upload-Routen.

FastAPI parst JSON-Bodies komplett (``json.loads`` auf dem Event-Loop), bevor
Rate-Limit-Dependency, PIN-Lockout und Schema-Pruefung greifen. Ein 55-MB-Body an
das PIN-freie /api/auth/login wurde zu ~1,5 GB Python-Objekten. Die Grenze muss
deshalb pro Route VOR dem Parsen greifen.
"""

import json
import re
from pathlib import Path

import pytest

from app.core import request_limits as rl
from tests.conftest import make_test_client


def test_body_limit_per_path():
    upload, _ = rl.body_limit_for_path("/api/documents/upload", 50)
    assert upload == rl.max_request_body_bytes(50)
    assert rl.body_limit_for_path("/api/auth/login", 50)[0] == rl.AUTH_MAX_BODY_BYTES
    assert rl.body_limit_for_path("/api/documents/abc", 50)[0] == rl.DEFAULT_MAX_BODY_BYTES
    # Kein Praefix-Match: Pfade unterhalb/neben der Upload-Route erben die
    # grosse Grenze nicht.
    assert rl.body_limit_for_path("/api/documents/upload/x", 50)[0] == rl.DEFAULT_MAX_BODY_BYTES
    assert rl.body_limit_for_path("/api/documents/uploadx", 50)[0] == rl.DEFAULT_MAX_BODY_BYTES
    assert rl.AUTH_MAX_BODY_BYTES < rl.DEFAULT_MAX_BODY_BYTES < upload


@pytest.fixture
async def pin_client(test_settings, test_engine, test_session_factory):
    settings = test_settings.model_copy(update={"PIN_ENABLED": True, "PIN_CODE": "1234"})
    async with make_test_client(settings, test_session_factory) as ac:
        yield ac


def _amplifying_body(size: int) -> bytes:
    # {"pin":"1","x":[{},{},...]} — billig zu senden, teuer zu parsen.
    n = max(1, (size - 20) // 3)
    return b'{"pin":"1","x":[' + b",".join([b"{}"] * n) + b"]}"


@pytest.mark.asyncio
async def test_login_rejects_oversized_body_before_parsing(pin_client, monkeypatch):
    parsed: list[int] = []
    real_loads = json.loads

    def spy_loads(data, *a, **kw):
        parsed.append(len(data))
        return real_loads(data, *a, **kw)

    monkeypatch.setattr(json, "loads", spy_loads)
    body = _amplifying_body(rl.AUTH_MAX_BODY_BYTES * 4)
    resp = await pin_client.post(
        "/api/auth/login", content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413, resp.text
    assert not any(n >= len(body) for n in parsed), "Body wurde trotz 413 geparst"


@pytest.mark.asyncio
async def test_login_rejects_oversized_streamed_body(pin_client):
    """Chunked ohne Content-Length: Abbruch beim Einlesen."""

    async def chunks():
        yield b'{"pin":"1","x":['
        for _ in range(4):
            yield b"{}," * 1024

    resp = await pin_client.post(
        "/api/auth/login", content=chunks(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413, resp.text


@pytest.mark.asyncio
async def test_login_with_normal_body_still_works(pin_client):
    resp = await pin_client.post("/api/auth/login", json={"pin": "1234"})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_non_upload_route_rejects_large_json(client):
    body = _amplifying_body(rl.DEFAULT_MAX_BODY_BYTES + 1024)
    resp = await client.post(
        "/api/filing-scopes", content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413, resp.text


@pytest.mark.asyncio
async def test_long_summary_update_below_default_limit_is_accepted(client):
    """Legitimer groesster JSON-Body (lange Zusammenfassung) bleibt erlaubt —
    die Route antwortet fachlich (404 fuer unbekanntes Dokument), nicht 413."""
    resp = await client.patch(
        "/api/documents/does-not-exist", json={"summary": "x" * 100_000}
    )
    assert resp.status_code != 413, resp.text


def test_nginx_mirrors_backend_limits():
    conf = (
        Path(__file__).resolve().parents[3] / "frontend" / "nginx-common.conf"
    ).read_text(encoding="utf-8")
    api_block = re.search(r"location \^~ /api/ \{(.*?)\n\}", conf, re.S).group(1)
    assert re.search(r"client_max_body_size\s+256k;", api_block), api_block
    assert rl.DEFAULT_MAX_BODY_BYTES == 256 * 1024
    for path in rl.UPLOAD_PATHS:
        block = re.search(r"location = " + re.escape(path) + r" \{(.*?)\n\}", conf, re.S)
        assert block, f"nginx-location fuer {path} fehlt"
        assert re.search(r"client_max_body_size\s+55M;", block.group(1))
        assert "X-Real-IP $remote_addr" in block.group(1)
