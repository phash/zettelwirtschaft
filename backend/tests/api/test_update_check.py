"""Tests fuer den opt-in Update-Check-Endpoint."""

import pytest


def _fake_result(**over):
    base = {
        "current_version": "1.3.1",
        "latest_version": "1.4.0",
        "update_available": True,
        "release_url": "https://zettelwirtschaft.mr-development.de/download.html",
        "published_at": "2026-06-16",
        "notes": "Update-Pruefung",
        "error": None,
        "cached": False,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
class TestUpdateCheck:
    async def test_disabled_by_default(self, client):
        resp = await client.get("/api/system/update-check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert isinstance(data["current_version"], str)
        assert data["latest_version"] is None
        assert data["update_available"] is False

    async def test_disabled_makes_no_external_call(self, client, monkeypatch):
        from app.services import update_service

        called = {"n": 0}

        async def boom(*a, **k):
            called["n"] += 1
            return _fake_result()

        monkeypatch.setattr(update_service, "check_for_update", boom)
        resp = await client.get("/api/system/update-check")
        assert resp.status_code == 200
        # Solange deaktiviert: NIE ein externer Aufruf (Telemetriefreiheit).
        assert called["n"] == 0

    async def test_enable_then_check(self, client, monkeypatch):
        from app.services import update_service

        async def fake(url, force=False, timeout=6.0):
            return _fake_result()

        monkeypatch.setattr(update_service, "check_for_update", fake)
        resp = await client.put("/api/system/update-check", json={"enabled": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["update_available"] is True
        assert data["latest_version"] == "1.4.0"
        assert data["checked_at"] is not None

        # Persistenz: nachfolgender GET ist weiterhin aktiviert
        resp2 = await client.get("/api/system/update-check")
        assert resp2.json()["enabled"] is True

    async def test_disable_again(self, client, monkeypatch):
        from app.services import update_service

        async def fake(url, force=False, timeout=6.0):
            return _fake_result()

        monkeypatch.setattr(update_service, "check_for_update", fake)
        await client.put("/api/system/update-check", json={"enabled": True})
        resp = await client.put("/api/system/update-check", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        # GET danach: deaktiviert, kein latest
        resp2 = await client.get("/api/system/update-check")
        assert resp2.json()["enabled"] is False
        assert resp2.json()["latest_version"] is None
