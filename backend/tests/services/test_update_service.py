"""Tests fuer die Update-Pruefung (update_service)."""

import httpx
import pytest

from app.services import update_service as us


class TestVersionParsing:
    def test_parse_basic(self):
        assert us.parse_version("1.4.0") == (1, 4, 0)
        assert us.parse_version("v1.4.0") == (1, 4, 0)
        assert us.parse_version("1.4.0-beta1") == (1, 4, 0)
        assert us.parse_version("1.4.0+build5") == (1, 4, 0)
        assert us.parse_version("2.0") == (2, 0)

    def test_parse_invalid(self):
        assert us.parse_version("") is None
        assert us.parse_version("unknown") is None
        assert us.parse_version("dev") is None
        assert us.parse_version(None) is None

    def test_is_newer(self):
        assert us.is_newer("1.3.1", "1.4.0") is True
        assert us.is_newer("1.4.0", "1.4.0") is False
        assert us.is_newer("1.4.0", "1.3.9") is False
        # numerischer (kein lexikalischer) Vergleich
        assert us.is_newer("1.3.1", "1.3.10") is True
        # unterschiedliche Stellenzahl
        assert us.is_newer("1.4", "1.4.1") is True
        assert us.is_newer("1.4.1", "1.4") is False

    def test_is_newer_unparsable(self):
        assert us.is_newer("unknown", "1.4.0") is False
        assert us.is_newer("1.4.0", "unknown") is False
        assert us.is_newer(None, None) is False


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return _FakeResp(self._payload)


@pytest.mark.asyncio
class TestCheckForUpdate:
    async def test_update_available(self, monkeypatch):
        us._reset_cache()
        monkeypatch.setattr(us, "read_local_version", lambda: "1.3.1")
        manifest = {
            "latest_version": "1.4.0",
            "release_url": "https://x/download.html",
            "published_at": "2026-06-16",
            "notes": "Update-Pruefung",
        }
        monkeypatch.setattr(us.httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload=manifest))
        res = await us.check_for_update("https://example/latest.json", force=True)
        assert res["latest_version"] == "1.4.0"
        assert res["update_available"] is True
        assert res["release_url"] == "https://x/download.html"
        assert res["error"] is None

    async def test_up_to_date(self, monkeypatch):
        us._reset_cache()
        monkeypatch.setattr(us, "read_local_version", lambda: "1.4.0")
        monkeypatch.setattr(
            us.httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload={"latest_version": "1.4.0"})
        )
        res = await us.check_for_update("https://example/latest.json", force=True)
        assert res["update_available"] is False
        assert res["error"] is None

    async def test_network_error_graceful(self, monkeypatch):
        us._reset_cache()
        monkeypatch.setattr(us, "read_local_version", lambda: "1.3.1")
        monkeypatch.setattr(
            us.httpx,
            "AsyncClient",
            lambda *a, **k: _FakeClient(exc=httpx.ConnectError("boom")),
        )
        res = await us.check_for_update("https://example/latest.json", force=True)
        assert res["error"] is not None
        assert res["update_available"] is False
        assert res["latest_version"] is None

    async def test_manifest_without_version(self, monkeypatch):
        us._reset_cache()
        monkeypatch.setattr(us, "read_local_version", lambda: "1.3.1")
        monkeypatch.setattr(
            us.httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload={"foo": "bar"})
        )
        res = await us.check_for_update("https://example/latest.json", force=True)
        assert res["error"] is not None
        assert res["latest_version"] is None
