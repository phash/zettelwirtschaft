"""Tests fuer Garantie-Tracker API."""

import pytest


@pytest.mark.asyncio
class TestWarrantyList:
    async def test_list_empty(self, client):
        resp = await client.get("/api/warranties")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_filter_active(self, client):
        resp = await client.get("/api/warranties?status=active")
        assert resp.status_code == 200

    async def test_list_filter_expired(self, client):
        resp = await client.get("/api/warranties?status=expired")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestWarrantyStats:
    async def test_stats_empty(self, client):
        resp = await client.get("/api/warranties/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["active"] == 0
        assert data["expiring_soon"] == 0
        assert data["expired"] == 0


@pytest.mark.asyncio
class TestWarrantyUpdate:
    async def test_update_not_found(self, client):
        resp = await client.patch("/api/warranties/nonexistent", json={"notes": "test"})
        assert resp.status_code == 404
