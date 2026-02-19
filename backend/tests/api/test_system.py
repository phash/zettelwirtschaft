"""Tests fuer System-Health und Backup-API."""

import pytest


@pytest.mark.asyncio
class TestSystemHealth:
    async def test_health_check(self, client):
        resp = await client.get("/api/system/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        assert "statistics" in data
        assert data["components"]["backend"]["status"] == "ok"
        assert data["components"]["database"]["status"] == "ok"


@pytest.mark.asyncio
class TestSystemBackup:
    async def test_create_backup(self, client):
        resp = await client.post("/api/system/backup")
        assert resp.status_code == 200
        assert "path" in resp.json()

    async def test_list_backups(self, client):
        resp = await client.get("/api/system/backups")
        assert resp.status_code == 200
        assert "backups" in resp.json()


@pytest.mark.asyncio
class TestSystemMaintenance:
    async def test_rebuild_index(self, client):
        resp = await client.post("/api/system/maintenance/rebuild-index")
        assert resp.status_code == 200
