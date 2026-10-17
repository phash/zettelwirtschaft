"""BR-02/BR-03/BR-04: CSRF per Fetch-Metadata, keine CORS-Freigabe fuer fremde
Origins, Security-Header im Native-Betrieb."""

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.auth import clear_all_sessions
from app.core.security_headers import CONTENT_SECURITY_POLICY, SECURITY_HEADERS
from app.models.processing_job import ProcessingJob
from tests.conftest import make_test_client

HOST = "192.168.178.20:8080"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
async def client(test_settings, test_engine, test_session_factory):
    async with make_test_client(test_settings, test_session_factory) as ac:
        yield ac


@pytest.fixture
async def pin_client(test_settings, test_engine, test_session_factory):
    clear_all_sessions()
    s = test_settings.model_copy(update={"PIN_ENABLED": True, "PIN_CODE": "1234"})
    async with make_test_client(s, test_session_factory) as ac:
        yield ac
    clear_all_sessions()


@pytest.fixture
async def native_client(test_settings, test_engine, test_session_factory, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    s = test_settings.model_copy(update={"FRONTEND_DIST_DIR": str(dist)})
    assert s.deployment_mode == "native"
    async with make_test_client(s, test_session_factory) as ac:
        yield ac


async def _job_count(session_factory) -> int:
    async with session_factory() as session:
        return (await session.execute(select(func.count()).select_from(ProcessingJob))).scalar_one()


# --- BR-02: Cross-Site-Schreibzugriffe bei abgeschaltetem PIN ---


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["cross-site", "same-site"])
async def test_cross_site_no_cors_upload_rejected(client, test_session_factory, site):
    """no-cors-Multipart von fremder Seite (ohne/mit verschleiertem Origin) -> 403, kein Job."""
    r = await client.post(
        "/api/documents/upload",
        files={"files": ("x.pdf", PDF, "application/pdf")},
        headers={"Host": HOST, "Sec-Fetch-Site": site},
    )
    assert r.status_code == 403
    assert await _job_count(test_session_factory) == 0


@pytest.mark.asyncio
async def test_cross_site_body_less_post_rejected(client):
    r = await client.post(
        "/api/jobs/pause",
        headers={
            "Host": HOST,
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 403
    # Same-site (andere Port-Origin auf demselben Rechner): Origin-Pruefung allein
    # liesse localhost:* durch — Sec-Fetch-Site nicht.
    r = await client.post(
        "/api/jobs/pause",
        headers={"Host": "localhost:8080", "Origin": "http://localhost:3000", "Sec-Fetch-Site": "same-site"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_same_origin_and_non_browser_writes_allowed(client):
    r = await client.post(
        "/api/jobs/pause",
        headers={"Host": HOST, "Origin": f"http://{HOST}", "Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 200
    r = await client.post("/api/jobs/resume", headers={"Host": HOST})  # curl/Skript: kein Sec-Fetch-Site
    assert r.status_code == 200
    r = await client.post("/api/jobs/resume", headers={"Host": HOST, "Sec-Fetch-Site": "none"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_cross_site_get_still_allowed(client):
    """Lesende Requests (Links, <img>) bleiben moeglich; Lesen verhindert fehlendes CORS."""
    r = await client.get("/api/health", headers={"Host": HOST, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


# --- BR-03: keine CORS-Freigabe (auch nicht localhost:* / RFC1918) ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin", ["http://localhost:3000", "http://192.168.1.10:8123", "http://10.0.0.5", "https://evil.example"]
)
async def test_no_cors_reflection(pin_client, origin):
    login = await pin_client.post("/api/auth/login", json={"pin": "1234"}, headers={"Host": "localhost:8080"})
    assert login.status_code == 200
    pin_client.cookies.set("zw_session", login.cookies["zw_session"])

    r = await pin_client.get("/api/auth/status", headers={"Host": "localhost:8080", "Origin": origin})
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-credentials" not in r.headers

    pre = await pin_client.options(
        "/api/documents",
        headers={
            "Host": "localhost:8080",
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in pre.headers
    assert "access-control-allow-credentials" not in pre.headers


def test_no_cors_middleware_registered():
    from fastapi.middleware.cors import CORSMiddleware

    from app.main import app

    assert all(m.cls is not CORSMiddleware for m in app.user_middleware)


# --- BR-04: Security-Header im Native-Betrieb ---


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/health", "/api/auth/status", "/", "/dokumente", "/api/documents/x/file"])
async def test_native_security_headers(native_client, path):
    r = await native_client.get(path, headers={"Host": HOST})
    for name, value in SECURITY_HEADERS.items():
        assert r.headers.get(name) == value, (path, name)
        assert len(r.headers.get_list(name)) == 1


@pytest.mark.asyncio
async def test_native_security_headers_on_host_guard_rejection(native_client):
    r = await native_client.get("/api/health", headers={"Host": "attacker.example"})
    assert r.status_code == 400
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"


@pytest.mark.asyncio
async def test_no_backend_security_headers_outside_native(client):
    """Docker: nginx setzt sie (keine Dopplung); Dev: kein Edge-Server."""
    r = await client.get("/api/health")
    assert "content-security-policy" not in r.headers


def test_csp_matches_nginx():
    """Eine Policy fuer Docker (nginx) und Native (Backend) — Drift-Schutz."""
    conf = (Path(__file__).resolve().parents[3] / "frontend" / "nginx-common.conf").read_text(encoding="utf-8")
    nginx = dict(re.findall(r'^\s*add_header\s+([\w-]+)\s+"([^"]*)"\s+always;', conf, re.MULTILINE))
    for name, value in SECURITY_HEADERS.items():
        assert nginx.get(name) == value, name
    assert SECURITY_HEADERS["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
