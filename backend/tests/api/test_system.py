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
class TestSystemSettings:
    async def test_get_settings(self, client):
        resp = await client.get("/api/system/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "watch_dir" in data
        assert "export_dir" in data
        assert "watch_dir_host" in data
        assert "export_dir_host" in data

    async def test_update_settings(self, client):
        resp = await client.put("/api/system/settings", json={"watch_dir": "/app/data/watch", "export_dir": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["watch_dir"] == "/app/data/watch"
        assert data["export_dir"] == ""

    async def test_settings_roundtrip(self, client):
        """Gespeicherte Einstellungen koennen wieder gelesen werden."""
        await client.put("/api/system/settings", json={"watch_dir": "/app/data/custom", "export_dir": "/app/data/out"})
        resp = await client.get("/api/system/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["watch_dir"] == "/app/data/custom"
        assert data["export_dir"] == "/app/data/out"

    async def test_host_path_sets_container_path(self, client):
        """Host-Pfad setzt automatisch Container-Pfad auf /app/external/..."""
        resp = await client.put("/api/system/settings", json={
            "watch_dir": "",
            "export_dir": "",
            "watch_dir_host": "V:\\Zettelwirtschaft\\eingang",
            "export_dir_host": "V:\\Zettelwirtschaft\\fertig",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["watch_dir"] == "/app/external/watch"
        assert data["export_dir"] == "/app/external/export"
        assert data["watch_dir_host"] == "V:\\Zettelwirtschaft\\eingang"
        assert data["export_dir_host"] == "V:\\Zettelwirtschaft\\fertig"
        assert data["restart_required"] is True

    async def test_host_path_roundtrip(self, client):
        """Host-Pfade werden gespeichert und koennen gelesen werden."""
        await client.put("/api/system/settings", json={
            "watch_dir": "",
            "export_dir": "",
            "watch_dir_host": "D:\\Docs\\watch",
            "export_dir_host": "",
        })
        resp = await client.get("/api/system/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["watch_dir_host"] == "D:\\Docs\\watch"
        assert data["watch_dir"] == "/app/external/watch"

    async def test_host_mounts_json_written(self, client):
        """Host-Mount-Konfiguration wird als .host-mounts.json geschrieben."""
        import json
        from pathlib import Path
        from unittest.mock import patch
        from app.api.system import HOST_MOUNTS_FILE

        # Temp-Pfad verwenden
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp_path = f.name

        try:
            with patch("app.api.system.HOST_MOUNTS_FILE", tmp_path):
                resp = await client.put("/api/system/settings", json={
                    "watch_dir": "",
                    "export_dir": "",
                    "watch_dir_host": "V:\\Watch",
                    "export_dir_host": "V:\\Export",
                })
                assert resp.status_code == 200

                content = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
                assert content["watch"] == "V:\\Watch"
                assert content["export"] == "V:\\Export"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def test_clear_host_path_no_restart(self, client):
        """Wenn Host-Pfade nicht geaendert werden, kein restart_required."""
        resp = await client.put("/api/system/settings", json={
            "watch_dir": "/app/data/watch",
            "export_dir": "",
            "watch_dir_host": "",
            "export_dir_host": "",
        })
        assert resp.status_code == 200
        assert resp.json()["restart_required"] is False


@pytest.mark.asyncio
class TestSystemMaintenance:
    async def test_rebuild_index(self, client):
        resp = await client.post("/api/system/maintenance/rebuild-index")
        assert resp.status_code == 200
