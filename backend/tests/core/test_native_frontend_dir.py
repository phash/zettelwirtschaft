"""INFRA-01: Im Native-Bundle kommt die Weboberflaeche immer aus
``<install>/frontend`` — ein FRONTEND_DIST_DIR aus einer (von einem lokalen
Benutzer manipulierten) config.toml darf den SPA-Catch-All nicht zu einem
PIN-freien Dateiserver fuer beliebige Ordner machen."""

import sys

import httpx
import pytest
from fastapi import FastAPI

from app.core.install_dir import frontend_dist_dir
from app.main import _mount_frontend


def _layout(tmp_path):
    install = tmp_path / "inst"
    (install / "backend").mkdir(parents=True)
    (install / "frontend").mkdir()
    (install / "frontend" / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    foreign = tmp_path / "fremd"
    foreign.mkdir()
    (foreign / "index.html").write_text("<html>FREMD</html>", encoding="utf-8")
    (foreign / "secret.txt").write_text("GEHEIM", encoding="utf-8")
    return install, foreign


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as ac:
        return await ac.get(path)


@pytest.mark.asyncio
async def test_frozen_bundle_ignores_configured_dist_dir(tmp_path, monkeypatch, test_settings):
    install, foreign = _layout(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "backend" / "zettelwirtschaft-backend.exe"))
    settings = test_settings.model_copy(update={"FRONTEND_DIST_DIR": str(foreign)})

    assert frontend_dist_dir(settings.FRONTEND_DIST_DIR) == install.resolve() / "frontend"

    app = FastAPI()
    _mount_frontend(app, settings)
    r = await _get(app, "/secret.txt")
    assert r.status_code == 200
    assert "GEHEIM" not in r.text
    assert "SPA" in r.text  # SPA-Fallback aus dem Programmordner
    r = await _get(app, "/")
    assert "SPA" in r.text and "FREMD" not in r.text


@pytest.mark.asyncio
async def test_frozen_bundle_without_configured_dir_still_serves_frontend(tmp_path, monkeypatch, test_settings):
    install, _ = _layout(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "backend" / "zettelwirtschaft-backend.exe"))
    app = FastAPI()
    _mount_frontend(app, test_settings.model_copy(update={"FRONTEND_DIST_DIR": ""}))
    r = await _get(app, "/dokumente")
    assert r.status_code == 200 and "SPA" in r.text


@pytest.mark.asyncio
async def test_dev_mode_keeps_configured_dist_dir(tmp_path, test_settings):
    """Ausserhalb des Bundles (Dev) bleibt FRONTEND_DIST_DIR wie bisher wirksam."""
    assert not getattr(sys, "frozen", False)
    _, foreign = _layout(tmp_path)
    app = FastAPI()
    _mount_frontend(app, test_settings.model_copy(update={"FRONTEND_DIST_DIR": str(foreign)}))
    r = await _get(app, "/secret.txt")
    assert r.status_code == 200 and r.text == "GEHEIM"
    # Path-Traversal aus dem Ordner heraus bleibt verboten.
    (tmp_path / "outside.txt").write_text("DRAUSSEN", encoding="utf-8")
    r = await _get(app, "/..%2Foutside.txt")
    assert "DRAUSSEN" not in r.text
