"""Client-IP-Ermittlung (NEW-002) und globales Rate-Limit.

Regressionen:
- X-Real-IP wurde aus allen privaten Netzen (auch 192.168/16, 10/8) als
  Proxy-Header uebernommen. Native (uvicorn direkt im LAN) liess damit jeden
  LAN-Client seine Lockout-Identitaet frei rotieren.
- Das globale 200/min-Limit (SlowAPIMiddleware) war seit FastAPI 0.143
  wirkungslos, weil die Middleware keinen Route-Handler mehr fand.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

import app.config
from app.config import Settings
from app.core.rate_limit import is_trusted_proxy, trusted_client_ip


def _request(host: str, real_ip: str | None = None) -> Request:
    headers = [(b"x-real-ip", real_ip.encode())] if real_ip is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (host, 4711)})


@pytest.fixture
def docker_mode(monkeypatch):
    monkeypatch.setattr(app.config, "_in_container", lambda: True)
    monkeypatch.setattr(app.config, "_is_frozen", lambda: False)


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setattr(app.config, "_in_container", lambda: False)
    monkeypatch.setattr(app.config, "_is_frozen", lambda: False)


class TestTrustedClientIp:
    def test_dev_ignores_real_ip_from_lan(self, dev_mode, test_settings):
        req = _request("192.168.1.50", "203.0.113.7")
        assert trusted_client_ip(req, test_settings) == "192.168.1.50"

    def test_dev_ignores_real_ip_even_from_localhost(self, dev_mode, test_settings):
        req = _request("127.0.0.1", "203.0.113.7")
        assert trusted_client_ip(req, test_settings) == "127.0.0.1"

    def test_native_ignores_real_ip_from_docker_range(self, monkeypatch, test_settings):
        monkeypatch.setattr(app.config, "_is_frozen", lambda: True)
        assert test_settings.deployment_mode == "native"
        req = _request("172.18.0.5", "203.0.113.7")
        assert trusted_client_ip(req, test_settings) == "172.18.0.5"

    def test_docker_trusts_real_ip_from_compose_network(self, docker_mode, test_settings):
        req = _request("172.18.0.5", "192.168.1.42")
        assert trusted_client_ip(req, test_settings) == "192.168.1.42"

    def test_docker_ignores_real_ip_from_lan_peer(self, docker_mode, test_settings):
        # 192.168/16 gehoert NICHT zu den Docker-Netzen (TRUSTED_PROXIES-Default).
        req = _request("192.168.1.50", "203.0.113.7")
        assert trusted_client_ip(req, test_settings) == "192.168.1.50"

    def test_docker_invalid_real_ip_falls_back_to_socket(self, docker_mode, test_settings):
        req = _request("172.18.0.5", "not-an-ip")
        assert trusted_client_ip(req, test_settings) == "172.18.0.5"

    def test_no_real_ip_uses_socket(self, docker_mode, test_settings):
        assert trusted_client_ip(_request("172.18.0.5"), test_settings) == "172.18.0.5"

    def test_custom_trusted_proxies(self, docker_mode, test_settings):
        settings = test_settings.model_copy(update={"TRUSTED_PROXIES": "10.10.0.0/24"})
        assert is_trusted_proxy("10.10.0.3", settings) is True
        assert is_trusted_proxy("172.18.0.5", settings) is False

    def test_invalid_trusted_proxies_rejected(self):
        with pytest.raises(ValueError, match="TRUSTED_PROXIES"):
            Settings(TRUSTED_PROXIES="kein-netz")


@pytest.mark.asyncio
class TestGlobalRateLimit:
    async def test_201st_request_is_limited(self, client):
        for i in range(200):
            resp = await client.get("/api/auth/status")
            assert resp.status_code == 200, f"Request {i + 1} faelschlich limitiert"
        resp = await client.get("/api/auth/status")
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) >= 1
        assert "Zu viele Anfragen" in resp.json()["detail"]

    async def test_health_is_exempt(self, client):
        for _ in range(205):
            resp = await client.get("/api/health")
            assert resp.status_code == 200

    async def test_limit_is_per_client_ip(self, client, test_settings):
        from app.main import app

        for _ in range(200):
            await client.get("/api/auth/status")
        assert (await client.get("/api/auth/status")).status_code == 429
        other = AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.77", 5000)), base_url="http://test")
        async with other:
            assert (await other.get("/api/auth/status")).status_code == 200
