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

    async def test_health_contains_app_version(self, client):
        resp = await client.get("/api/system/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "app_version" in data
        assert isinstance(data["app_version"], str)
        assert len(data["app_version"]) > 0

    async def test_health_app_version_from_file(self, client):
        """app_version wird aus data/.version gelesen wenn vorhanden."""
        from pathlib import Path
        data_dir = Path("./data")
        data_dir.mkdir(exist_ok=True)
        ver_file = data_dir / ".version"
        old_content = ver_file.read_text(encoding="utf-8") if ver_file.exists() else None
        ver_file.write_text("2.3.4", encoding="utf-8")
        try:
            resp = await client.get("/api/system/health")
            assert resp.status_code == 200
            assert resp.json()["app_version"] == "2.3.4"
        finally:
            if old_content is not None:
                ver_file.write_text(old_content, encoding="utf-8")
            else:
                ver_file.unlink(missing_ok=True)


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
