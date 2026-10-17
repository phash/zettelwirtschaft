"""Tests fuer System-Health, Ordner-Einstellungen, Backup- und Wartungs-API."""

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.config
from app.services.task_service import shutdown_tasks, wait_for_task


@pytest.fixture(autouse=True)
async def _stop_background_tasks():
    yield
    await shutdown_tasks()


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setattr(app.config, "_in_container", lambda: False)
    monkeypatch.setattr(app.config, "_is_frozen", lambda: False)


@pytest.fixture
def docker_mode(monkeypatch):
    monkeypatch.setattr(app.config, "_in_container", lambda: True)
    monkeypatch.setattr(app.config, "_is_frozen", lambda: False)


@pytest.fixture
def no_watch_restart(monkeypatch):
    """Watch-Task-Neustart abklemmen (kein echter Watcher im Test)."""
    calls = []

    async def fake_restart(app, settings):
        calls.append(settings)

    monkeypatch.setattr("app.api.system._restart_watch_task", fake_restart)
    return calls


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

    async def test_health_uses_shared_status_vocabulary(self, client, dev_mode):
        """Ollama/Chroma sind im Test nicht erreichbar -> warning (wie /api/health)."""
        data = (await client.get("/api/system/health")).json()
        assert data["deployment_mode"] == "dev"
        assert data["components"]["ollama"]["status"] == "warning"
        assert data["components"]["chromadb"]["status"] == "warning"
        assert data["status"] == "warning"
        allowed = {"ok", "warning", "error"}
        assert {c["status"] for c in data["components"].values()} <= allowed

    async def test_health_reports_configured_models_exact_tag(self, client, test_settings):
        """Entscheidung 1: benoetigte Modelle mit Verfuegbarkeit (exakter Tag),
        fehlendes Modell als eigene Meldung."""
        from unittest.mock import AsyncMock, patch

        from app.services.ollama_client import OllamaProbe

        installed = [test_settings.EMBEDDING_MODEL, "qwen2.5:3b"]
        probe = OllamaProbe(reachable=True, models=installed, missing=[test_settings.OLLAMA_MODEL])
        with patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=probe)):
            ollama = (await client.get("/api/system/health")).json()["components"]["ollama"]

        assert ollama["status"] == "warning"
        assert f"{test_settings.OLLAMA_MODEL} nicht installiert" in ollama["message"]
        by_purpose = {m["purpose"]: m for m in ollama["configured_models"]}
        assert by_purpose["llm"] == {
            "name": test_settings.OLLAMA_MODEL, "purpose": "llm", "available": False, "downloading": False,
        }
        assert by_purpose["embedding"]["available"] is True
        assert ollama["missing"] == [test_settings.OLLAMA_MODEL]
        assert "models" not in ollama  # keine komplette Modellliste mehr

    async def test_health_reports_ollama_version_warning(self, client, test_settings):
        """Zu altes Ollama: Warnung (kein Fehler) + Version/Mindestversion fuer die UI."""
        from unittest.mock import AsyncMock, patch

        from app.services.ollama_client import OLLAMA_MIN_VERSION, OllamaProbe

        models = [test_settings.OLLAMA_MODEL, test_settings.EMBEDDING_MODEL]
        probe = OllamaProbe(reachable=True, models=models, missing=[])
        with patch("app.services.ollama_client.probe_ollama", AsyncMock(return_value=probe)), \
             patch("app.services.ollama_client.get_ollama_version", AsyncMock(return_value="0.12.6")):
            ollama = (await client.get("/api/system/health")).json()["components"]["ollama"]

        assert ollama["status"] == "warning"
        assert ollama["message"] == f"Ollama 0.12.6 ist älter als {OLLAMA_MIN_VERSION} — bitte aktualisieren"
        assert ollama["version"] == "0.12.6"
        assert ollama["min_version"] == OLLAMA_MIN_VERSION

    async def test_health_hides_database_exception(self, client, monkeypatch):
        """S-LOW5: keine rohen DB-Exceptions (Pfade/Treiber) in der Antwort."""
        from sqlalchemy.ext.asyncio import AsyncSession

        real_execute = AsyncSession.execute

        async def failing_execute(self, statement, *args, **kwargs):
            if "SELECT 1" in str(statement):
                raise RuntimeError("unable to open database file C:\\Users\\Max\\geheim.db")
            return await real_execute(self, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "execute", failing_execute)
        data = (await client.get("/api/system/health")).json()
        assert data["components"]["database"] == {"status": "error", "message": "Datenbankfehler"}
        assert data["status"] == "error"
        assert "geheim" not in json.dumps(data)

    async def test_health_contains_app_version(self, client):
        resp = await client.get("/api/system/health")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["app_version"], str)
        assert len(data["app_version"]) > 0

    async def test_health_app_version_from_file(self, client, tmp_path, monkeypatch):
        """app_version wird aus data/.version gelesen (CWD = isoliertes tmp)."""
        cwd = tmp_path / "cwd"
        (cwd / "data").mkdir(parents=True)
        (cwd / "data" / ".version").write_text("2.3.4", encoding="utf-8")
        monkeypatch.chdir(cwd)
        resp = await client.get("/api/system/health")
        assert resp.json()["app_version"] == "2.3.4"

    @pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])  # install.ps1 (PS 5.1) schreibt mit BOM
    async def test_install_path_read_from_data_dir(self, client, test_settings, encoding):
        (test_settings.data_dir / ".install-path").write_text("C:\\Programme\\Zettelwirtschaft\n", encoding=encoding)
        data = (await client.get("/api/system/health")).json()
        assert data["install_path"] == "C:\\Programme\\Zettelwirtschaft"

    async def test_system_info_is_cached(self, client, monkeypatch):
        import app.api.system as system

        calls = []
        real = system.get_system_info

        def counting(settings):
            calls.append(1)
            return real(settings)

        monkeypatch.setattr(system, "get_system_info", counting)
        await client.get("/api/system/health")
        await client.get("/api/system/health")
        assert len(calls) == 1


@pytest.mark.asyncio
class TestErrorContract:
    async def test_unhandled_exception_returns_json(self, client, monkeypatch):
        """Unerwartete Fehler -> JSON {"detail": ...} statt text/plain."""
        from app.main import app as fastapi_app

        async def boom(settings):
            raise RuntimeError("kaputt: C:\\geheim")

        monkeypatch.setattr("app.api.system._cached_system_info", boom)
        transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/system/health")
        assert resp.status_code == 500
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == {"detail": "Interner Serverfehler"}

    async def test_failed_commit_is_reported_before_response(self, client, test_session_factory):
        """get_db laeuft mit scope="function": ein Commit-Fehler muss als 500
        beim Client ankommen statt nach einer bereits gesendeten 200."""
        from app.database import get_db
        from app.main import app as fastapi_app

        async def failing_commit_db():
            async with test_session_factory() as session:
                yield session
                raise RuntimeError("database is locked")

        fastapi_app.dependency_overrides[get_db] = failing_commit_db
        transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/system/settings")
        assert resp.status_code == 500


@pytest.mark.asyncio
class TestRequestBodyLimit:
    async def test_declared_oversize_body_is_rejected(self, client, test_settings):
        from app.core.request_limits import max_request_body_bytes

        too_big = max_request_body_bytes(test_settings.MAX_UPLOAD_SIZE_MB) + 1
        resp = await client.post(
            "/api/documents/upload",
            content=b"x",
            headers={"content-length": str(too_big), "content-type": "multipart/form-data; boundary=x"},
        )
        assert resp.status_code == 413, resp.text
        assert "50 MB" in resp.json()["detail"]

    async def test_streamed_oversize_body_is_rejected(self, client, test_settings, monkeypatch):
        """Chunked-Upload ohne Content-Length: Abbruch waehrend des Einlesens."""
        import app.core.request_limits as rl

        monkeypatch.setattr(rl, "MULTIPART_HEADROOM_MB", 0)
        small = test_settings.model_copy(update={"MAX_UPLOAD_SIZE_MB": 1})
        from app.config import get_settings
        from app.main import app as fastapi_app

        fastapi_app.dependency_overrides[get_settings] = lambda: small

        async def chunks():
            # gueltiger Multipart-Anfang, damit der Parser nicht vorher mit 400 abbricht
            yield (
                b"--x\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.pdf\"\r\n"
                b"Content-Type: application/pdf\r\n\r\n"
            )
            for _ in range(3):
                yield b"y" * (512 * 1024)

        resp = await client.post(
            "/api/documents/upload",
            content=chunks(),
            headers={"content-type": "multipart/form-data; boundary=x"},
        )
        assert resp.status_code == 413, resp.text
        assert "1 MB" in resp.json()["detail"]


@pytest.mark.asyncio
class TestSystemBackup:
    async def test_create_backup_runs_as_background_task(self, client):
        resp = await client.post("/api/system/backup")
        assert resp.status_code == 202
        task = resp.json()
        assert task["kind"] == "backup"
        await wait_for_task(task["id"], timeout=10)
        final = (await client.get(f"/api/tasks/{task['id']}")).json()
        assert final["status"] == "SUCCEEDED", final
        assert final["result"]["filename"].startswith("backup_db_")
        assert final["result"]["is_full"] is False

        backups = (await client.get("/api/system/backups")).json()["backups"]
        assert [b["filename"] for b in backups] == [final["result"]["filename"]]

    async def test_full_backup_contains_documents(self, client, test_settings):
        import zipfile

        doc = Path(test_settings.ARCHIVE_DIR) / "privat" / "2024" / "a.pdf"
        doc.parent.mkdir(parents=True)
        doc.write_bytes(b"%PDF-1.4")
        task = (await client.post("/api/system/backup", params={"full": True})).json()
        await wait_for_task(task["id"], timeout=10)
        final = (await client.get(f"/api/tasks/{task['id']}")).json()
        assert final["status"] == "SUCCEEDED", final
        path = test_settings.data_dir / "backups" / final["result"]["filename"]
        with zipfile.ZipFile(path) as zf:
            assert "documents/privat/2024/a.pdf" in zf.namelist()

    async def test_backup_failure_is_reported_in_task(self, client, monkeypatch):
        def broken(settings, full):
            raise FileNotFoundError("weg")

        monkeypatch.setattr("app.api.system.create_backup", broken)
        task = (await client.post("/api/system/backup")).json()
        await wait_for_task(task["id"], timeout=10)
        final = (await client.get(f"/api/tasks/{task['id']}")).json()
        assert final["status"] == "FAILED"
        assert "Datenbankdatei" in final["error"]

    async def test_second_backup_while_running_conflicts(self, client, monkeypatch):
        import threading

        release = threading.Event()

        def slow(settings, full):
            release.wait(5)
            raise FileNotFoundError("abgebrochen")

        monkeypatch.setattr("app.api.system.create_backup", slow)
        first = await client.post("/api/system/backup")
        assert first.status_code == 202
        second = await client.post("/api/system/backup", params={"full": True})
        assert second.status_code == 409
        assert second.json()["task"]["id"] == first.json()["id"]
        release.set()
        await wait_for_task(first.json()["id"], timeout=10)

    async def test_list_backups_has_no_server_paths(self, client, test_settings):
        backup_dir = test_settings.data_dir / "backups"
        backup_dir.mkdir()
        old_full = backup_dir / "backup_full_20240101_000000.zip"
        new_db = backup_dir / "backup_db_20240102_000000.zip"
        for f in (old_full, new_db):
            f.write_bytes(b"PK")
        now = time.time()
        os.utime(old_full, (now - 7200, now - 7200))
        os.utime(new_db, (now, now))

        backups = (await client.get("/api/system/backups")).json()["backups"]
        # chronologisch (mtime), nicht nach Dateiname ('full' > 'db')
        assert [b["filename"] for b in backups] == [new_db.name, old_full.name]
        assert all("path" not in b for b in backups)
        assert backups[0]["created_at"].endswith("+00:00")
        assert backups[1]["is_full"] is True


@pytest.mark.asyncio
class TestBackupDownload:
    async def test_download_zip(self, client, test_settings):
        backup_dir = test_settings.data_dir / "backups"
        backup_dir.mkdir()
        (backup_dir / "backup_db_20240101_000000.zip").write_bytes(b"PK\x03\x04")
        resp = await client.get("/api/system/backups/backup_db_20240101_000000.zip")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert resp.content == b"PK\x03\x04"

    async def test_traversal_is_rejected(self, client, test_settings):
        (test_settings.data_dir / "backups").mkdir()
        (test_settings.data_dir / "geheim.zip").write_bytes(b"x")
        resp = await client.get("/api/system/backups/..%5Cgeheim.zip")
        assert resp.status_code in (400, 404)
        assert resp.content != b"x"

    async def test_wrong_prefix_or_missing_is_404(self, client, test_settings):
        backup_dir = test_settings.data_dir / "backups"
        backup_dir.mkdir()
        (backup_dir / "other.zip").write_bytes(b"x")
        assert (await client.get("/api/system/backups/other.zip")).status_code == 404
        assert (await client.get("/api/system/backups/backup_db_fehlt.zip")).status_code == 404


@pytest.mark.asyncio
class TestSystemMaintenance:
    async def test_rebuild_index(self, client):
        resp = await client.post("/api/system/maintenance/rebuild-index")
        assert resp.status_code == 200

    async def test_optimize_db(self, client, test_settings):
        resp = await client.post("/api/system/maintenance/optimize-db")
        assert resp.status_code == 200
        conn = sqlite3.connect(test_settings.sqlite_db_path)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        finally:
            conn.close()

    async def test_rebuild_vectors_runs_as_background_task(self, client):
        from unittest.mock import AsyncMock, patch

        result = {"total": 2, "processed": 2, "chunks": 5, "failed": 0}
        with patch("app.services.vectorize_service.vectorize_all_documents", AsyncMock(return_value=result)):
            resp = await client.post("/api/system/maintenance/rebuild-vectors")
            assert resp.status_code == 202
            task = resp.json()
            assert (task["kind"], task["key"]) == ("rebuild_vectors", "global")
            await wait_for_task(task["id"], timeout=10)
        final = (await client.get(f"/api/tasks/{task['id']}")).json()
        assert final["status"] == "SUCCEEDED", final
        assert final["result"] == result

    async def test_rebuild_vectors_nothing_embedded_fails(self, client):
        from unittest.mock import AsyncMock, patch

        result = {"total": 2, "processed": 2, "chunks": 0, "failed": 2}
        with patch("app.services.vectorize_service.vectorize_all_documents", AsyncMock(return_value=result)):
            task = (await client.post("/api/system/maintenance/rebuild-vectors")).json()
            await wait_for_task(task["id"], timeout=10)
        final = (await client.get(f"/api/tasks/{task['id']}")).json()
        assert final["status"] == "FAILED"
        assert "Embedding-Modell" in final["error"]

    async def test_rebuild_vectors_conflict_returns_running_task(self, client):
        import asyncio
        from unittest.mock import patch

        release = asyncio.Event()

        async def slow(*args, **kwargs):
            await release.wait()
            return {"total": 0, "processed": 0, "chunks": 0, "failed": 0}

        with patch("app.services.vectorize_service.vectorize_all_documents", slow):
            first = await client.post("/api/system/maintenance/rebuild-vectors")
            second = await client.post("/api/system/maintenance/rebuild-vectors")
            assert second.status_code == 409
            assert second.json()["task"]["id"] == first.json()["id"]
            assert second.json()["detail"] == "Der Vektor-Index wird bereits neu aufgebaut."
            release.set()
            await wait_for_task(first.json()["id"], timeout=10)
        # Der fruehere eigene Status-Endpoint ist durch /api/tasks ersetzt.
        assert (await client.get("/api/system/maintenance/rebuild-vectors/status")).status_code in (404, 405)


@pytest.mark.asyncio
class TestFolderSettingsDev:
    """Native/Dev: das Backend greift direkt auf die Ordner zu."""

    async def test_get_settings(self, client, dev_mode):
        resp = await client.get("/api/system/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployment_mode"] == "dev"
        assert {"watch_dir", "export_dir", "watch_dir_host", "export_dir_host"} <= set(data)

    async def test_paths_are_stored_and_created(self, client, dev_mode, tmp_path, no_watch_restart):
        watch = tmp_path / "eingang"
        export = tmp_path / "ausgang" / "steuer"
        resp = await client.put("/api/system/settings", json={"watch_dir": str(watch), "export_dir": str(export)})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["watch_dir"] == str(watch)
        assert data["export_dir"] == str(export)
        assert data["restart_required"] is False
        assert watch.is_dir() and export.is_dir()
        assert len(no_watch_restart) == 1

        again = (await client.get("/api/system/settings")).json()
        assert again["watch_dir"] == str(watch)
        assert again["export_dir"] == str(export)

    async def test_host_path_is_used_directly(self, client, dev_mode, tmp_path, test_settings, no_watch_restart):
        """Regression: Host-Pfade wurden auch ausserhalb von Docker auf
        /app/external/* umgebogen (<Laufwerk>:\\app\\external\\watch)."""
        watch = tmp_path / "scanner"
        resp = await client.put("/api/system/settings", json={
            "watch_dir": test_settings.WATCH_DIR, "export_dir": "",
            "watch_dir_host": str(watch), "export_dir_host": "",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["watch_dir"] == str(watch)
        assert data["watch_dir_host"] == ""
        assert data["restart_required"] is False
        assert not (test_settings.data_dir / ".host-mounts.json").exists()

    async def test_quotes_and_whitespace_are_stripped(self, client, dev_mode, tmp_path, no_watch_restart):
        watch = tmp_path / "mit leerzeichen"
        resp = await client.put("/api/system/settings", json={"watch_dir": f'  "{watch}"  ', "export_dir": ""})
        assert resp.status_code == 200, resp.text
        assert resp.json()["watch_dir"] == str(watch)

    async def test_empty_watch_dir_falls_back_to_default(self, client, dev_mode, test_settings, no_watch_restart):
        resp = await client.put("/api/system/settings", json={"watch_dir": "", "export_dir": ""})
        assert resp.status_code == 200
        assert resp.json()["watch_dir"] == test_settings.WATCH_DIR

    async def test_legacy_docker_paths_reset_to_default(self, client, dev_mode, test_settings, no_watch_restart):
        resp = await client.put("/api/system/settings", json={
            "watch_dir": "/app/external/watch", "export_dir": "/app/external/export",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["watch_dir"] == test_settings.WATCH_DIR
        assert resp.json()["export_dir"] == ""

    @pytest.mark.parametrize("bad", [
        "relativ/ordner",
        "C:\\Windows\\System32" if os.name == "nt" else "/etc/zettel",
        "C:\\" if os.name == "nt" else "/",
    ])
    async def test_invalid_paths_are_rejected(self, client, dev_mode, bad, no_watch_restart):
        resp = await client.put("/api/system/settings", json={"watch_dir": bad, "export_dir": ""})
        assert resp.status_code == 400, resp.text

    async def test_parent_segments_rejected(self, client, dev_mode, tmp_path, no_watch_restart):
        resp = await client.put("/api/system/settings", json={
            "watch_dir": str(tmp_path / "a" / ".." / "b"), "export_dir": "",
        })
        assert resp.status_code == 400

    async def test_app_managed_dirs_rejected(self, client, dev_mode, test_settings, no_watch_restart):
        for bad in (
            str(test_settings.data_dir),            # Datenordner selbst
            str(test_settings.data_dir.parent),     # Elternordner des Datenordners
            str(Path(test_settings.ARCHIVE_DIR) / "privat"),  # im Archiv
            test_settings.UPLOAD_DIR,
        ):
            resp = await client.put("/api/system/settings", json={"watch_dir": bad, "export_dir": ""})
            assert resp.status_code == 400, bad
            resp = await client.put("/api/system/settings", json={"watch_dir": "", "export_dir": bad})
            assert resp.status_code == 400, bad

    async def test_invalid_input_changes_nothing(self, client, dev_mode, tmp_path, no_watch_restart):
        good = tmp_path / "gut"
        await client.put("/api/system/settings", json={"watch_dir": str(good), "export_dir": ""})
        resp = await client.put("/api/system/settings", json={"watch_dir": str(tmp_path / "neu"), "export_dir": "relativ"})
        assert resp.status_code == 400
        assert (await client.get("/api/system/settings")).json()["watch_dir"] == str(good)


@pytest.mark.asyncio
class TestFolderSettingsDocker:
    async def test_host_path_sets_container_path(self, client, docker_mode, test_settings, no_watch_restart):
        """Docker: Host-Pfad setzt automatisch den Container-Pfad /app/external/..."""
        resp = await client.put("/api/system/settings", json={
            "watch_dir": "",
            "export_dir": "",
            "watch_dir_host": "V:\\Zettelwirtschaft\\eingang",
            "export_dir_host": "V:\\Zettelwirtschaft\\fertig",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["deployment_mode"] == "docker"
        assert data["watch_dir"] == "/app/external/watch"
        assert data["export_dir"] == "/app/external/export"
        assert data["watch_dir_host"] == "V:\\Zettelwirtschaft\\eingang"
        assert data["export_dir_host"] == "V:\\Zettelwirtschaft\\fertig"
        assert data["restart_required"] is True

        mounts = json.loads((test_settings.data_dir / ".host-mounts.json").read_text(encoding="utf-8"))
        assert mounts == {"watch": "V:\\Zettelwirtschaft\\eingang", "export": "V:\\Zettelwirtschaft\\fertig"}

        again = (await client.get("/api/system/settings")).json()
        assert again["watch_dir_host"] == "V:\\Zettelwirtschaft\\eingang"
        assert again["watch_dir"] == "/app/external/watch"

    async def test_clearing_host_path_restores_default(self, client, docker_mode, test_settings, no_watch_restart):
        await client.put("/api/system/settings", json={
            "watch_dir": "", "export_dir": "", "watch_dir_host": "D:\\Docs\\watch", "export_dir_host": "",
        })
        resp = await client.put("/api/system/settings", json={
            "watch_dir": "/app/external/watch", "export_dir": "", "watch_dir_host": "", "export_dir_host": "",
        })
        data = resp.json()
        assert data["watch_dir"] == test_settings.WATCH_DIR
        assert data["restart_required"] is True
        assert not (test_settings.data_dir / ".host-mounts.json").exists()

    async def test_unchanged_host_paths_need_no_restart(self, client, docker_mode, test_settings, no_watch_restart):
        resp = await client.put("/api/system/settings", json={
            "watch_dir": test_settings.WATCH_DIR, "export_dir": "", "watch_dir_host": "", "export_dir_host": "",
        })
        assert resp.status_code == 200
        assert resp.json()["restart_required"] is False

    @pytest.mark.parametrize("bad", ["C:\\Windows\\System32", "/etc", "V:\\", "relativ\\pfad", "D:\\a\\..\\b"])
    async def test_invalid_host_paths_rejected(self, client, docker_mode, bad, no_watch_restart):
        resp = await client.put("/api/system/settings", json={
            "watch_dir": "", "export_dir": "", "watch_dir_host": bad, "export_dir_host": "",
        })
        assert resp.status_code == 400, bad


@pytest.mark.asyncio
async def test_native_hides_stale_host_fields(client, monkeypatch, test_session_factory):
    monkeypatch.setattr(app.config, "_is_frozen", lambda: True)
    from app.services.settings_service import set_db_setting

    async with test_session_factory() as s:
        await set_db_setting(s, "watch_dir_host", "V:\\alt")
        await s.commit()
    data = (await client.get("/api/system/settings")).json()
    assert data["deployment_mode"] == "native"
    assert data["watch_dir_host"] == ""
