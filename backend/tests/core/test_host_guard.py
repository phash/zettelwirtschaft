"""AUTH-02/AUTH-03: Host-/Origin-Pruefung (DNS-Rebinding, CSRF) und keine
ungeschuetzte API-Doku."""

import pytest

from app.config import Settings
from app.core import host_guard
from tests.conftest import make_test_client


@pytest.fixture
def plain_settings() -> Settings:
    return Settings()


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8080",
        "127.0.0.1:8080",
        "[::1]:8080",
        "192.168.178.20:8080",
        "100.101.102.103",  # Tailscale o.ae.: IP-Literal, kein Rebinding moeglich
        "zettelwirtschaft.local",
        "DESKTOP-ABC123",
        "desktop-abc123.fritz.box:8080",
        "nas.home.arpa",
        "backend:8000",  # Docker-Dienstname
        "test",
    ],
)
def test_local_hosts_allowed(host, plain_settings):
    assert host_guard.is_allowed_host(host, plain_settings)


@pytest.mark.parametrize("host", ["attacker.example", "evil.com:8080", "rebind.attacker.example.", "local.evil.com"])
def test_foreign_hosts_rejected(host, plain_settings):
    assert not host_guard.is_allowed_host(host, plain_settings)


def test_machine_name_allowed(monkeypatch, plain_settings):
    host_guard._machine_names.cache_clear()
    monkeypatch.setattr(host_guard.socket, "gethostname", lambda: "Server-PC")
    monkeypatch.setattr(host_guard.socket, "getfqdn", lambda: "server-pc.corp.example")
    try:
        assert host_guard.is_allowed_host("server-pc.corp.example", plain_settings)
        assert not host_guard.is_allowed_host("other.corp.example", plain_settings)
    finally:
        host_guard._machine_names.cache_clear()


def test_allowed_hosts_setting():
    s = Settings(ALLOWED_HOSTS="dms.example.org, *.meine-domain.de")
    assert host_guard.is_allowed_host("dms.example.org", s)
    assert host_guard.is_allowed_host("a.meine-domain.de:443", s)
    assert not host_guard.is_allowed_host("meine-domain.de.evil.com", s)
    assert host_guard.is_allowed_host("anything.example", Settings(ALLOWED_HOSTS="*"))


@pytest.mark.parametrize(
    "origin,host,ok",
    [
        ("http://192.168.1.10:8080", "192.168.1.10:8080", True),
        ("http://zettelwirtschaft.local:8080", "zettelwirtschaft.local", True),
        ("http://localhost:3000", "localhost:8000", True),  # Vite-Dev-Proxy
        ("http://100.64.1.2:8080", "100.64.1.2:8080", True),  # same-origin
        ("https://attacker.example", "192.168.1.10:8080", False),
        ("http://8.8.8.8", "192.168.1.10:8080", False),
        ("null", "192.168.1.10:8080", False),
    ],
)
def test_origin_policy(origin, host, ok, plain_settings):
    assert host_guard.is_allowed_origin(origin, host, plain_settings) is ok


# --- Integration gegen die echte App ---


@pytest.fixture
async def pin_off_client(test_settings, test_engine, test_session_factory):
    async with make_test_client(test_settings, test_session_factory) as ac:
        yield ac


@pytest.mark.asyncio
async def test_rebound_host_is_rejected(pin_off_client):
    """DNS-Rebinding: fremder Host-Header -> 400, auch fuer Health/Auth."""
    for path in ("/api/documents", "/api/health", "/api/auth/status"):
        r = await pin_off_client.get(path, headers={"Host": "attacker.example"})
        assert r.status_code == 400, path
    r = await pin_off_client.post(
        "/api/auth/login", json={"pin": "0000"}, headers={"Host": "attacker.example:8080"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_lan_host_is_served(pin_off_client):
    r = await pin_off_client.get("/api/auth/status", headers={"Host": "192.168.178.20:8080"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_cross_site_post_is_rejected(pin_off_client):
    """CSRF bei abgeschaltetem PIN: POST von fremder Seite -> 403."""
    r = await pin_off_client.post(
        "/api/auth/logout",
        headers={"Host": "192.168.1.10:8080", "Origin": "https://attacker.example"},
    )
    assert r.status_code == 403
    r = await pin_off_client.post(
        "/api/auth/logout",
        headers={"Host": "192.168.1.10:8080", "Origin": "http://192.168.1.10:8080"},
    )
    assert r.status_code == 200
    # Lesende Requests mit fremdem Origin bleiben Sache von CORS.
    r = await pin_off_client.get(
        "/api/auth/status", headers={"Host": "192.168.1.10:8080", "Origin": "https://attacker.example"}
    )
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_not_served_by_default(pin_off_client, path):
    """AUTH-03: keine API-Landkarte ohne PIN (Native: PIN schuetzt nur /api/*)."""
    r = await pin_off_client.get(path)
    assert r.status_code == 404
    assert "openapi" not in r.text.lower()
    assert "swagger" not in r.text.lower()


def test_api_docs_disabled_on_app():
    from app.main import app

    assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None
