from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import clear_all_sessions
from app.config import Settings
from tests.conftest import make_test_client
from tests.credentials import TEST_PIN, TEST_WRONG_PIN


@pytest.fixture
def pin_settings(test_settings: Settings) -> Settings:
    """Settings with PIN enabled."""
    return test_settings.model_copy(
        update={"PIN_ENABLED": True, "PIN_CODE": TEST_PIN, "PIN_SESSION_TIMEOUT_MINUTES": 60}
    )


@pytest.fixture
async def pin_client(pin_settings, test_engine, test_session_factory):
    clear_all_sessions()
    async with make_test_client(pin_settings, test_session_factory) as ac:
        yield ac
    clear_all_sessions()


# --- Tests with PIN disabled (default) ---


@pytest.mark.asyncio
async def test_auth_status_pin_disabled(client):
    """When PIN is disabled, status returns not enabled + authenticated + pin_warning."""
    resp = await client.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pin_enabled"] is False
    assert data["authenticated"] is True
    # B1: Frontend nutzt pin_warning um Banner anzuzeigen.
    assert data["pin_warning"] is True


@pytest.mark.asyncio
async def test_middleware_allows_all_when_pin_disabled(client):
    """When PIN is disabled, all endpoints are accessible without auth."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200


# --- Tests with PIN enabled ---


@pytest.mark.asyncio
async def test_auth_status_pin_enabled_not_authenticated(pin_client):
    """When PIN is enabled but not logged in, status shows not authenticated."""
    resp = await pin_client.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pin_enabled"] is True
    assert data["authenticated"] is False
    assert data["pin_warning"] is False


@pytest.mark.asyncio
async def test_login_correct_pin(pin_client):
    """Login with correct PIN succeeds and sets cookie."""
    resp = await pin_client.post("/api/auth/login", json={"pin": TEST_PIN})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "zw_session" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_pin(pin_client):
    """Login with wrong PIN fails."""
    resp = await pin_client.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
    assert resp.status_code == 401
    data = resp.json()
    assert data["success"] is False
    assert "zw_session" not in resp.cookies


@pytest.mark.asyncio
async def test_middleware_blocks_without_auth(pin_client):
    """Protected endpoints return 401 without a valid session."""
    resp = await pin_client.get("/api/documents")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_always_accessible(pin_client):
    """Health endpoint is accessible even with PIN enabled and no session."""
    resp = await pin_client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_endpoints_always_accessible(pin_client):
    """Auth endpoints are accessible without a valid session."""
    resp = await pin_client.get("/api/auth/status")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_full_login_flow(pin_client):
    """Login, access protected endpoint, logout, verify blocked again."""
    # Login
    login_resp = await pin_client.post("/api/auth/login", json={"pin": TEST_PIN})
    assert login_resp.json()["success"] is True

    token = login_resp.cookies["zw_session"]
    pin_client.cookies.set("zw_session", token)

    # Access protected endpoint (NICHT whitelisted — muss die Middleware passieren)
    resp = await pin_client.get("/api/documents")
    assert resp.status_code == 200

    # Logout
    logout_resp = await pin_client.post("/api/auth/logout")
    assert logout_resp.status_code == 200

    # Das alte Token muss serverseitig ungueltig sein — auch wenn ein Client es
    # weiter mitschickt.
    pin_client.cookies.set("zw_session", token)
    resp = await pin_client.get("/api/documents")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_when_pin_disabled(client):
    """Login when PIN is disabled always succeeds."""
    resp = await client.post("/api/auth/login", json={"pin": "anything"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# Re-Review Test A: PIN Rate-Limit + Lockout (5/30s) war seit erster Runde ungetestet.
@pytest.mark.asyncio
async def test_pin_lockout_after_5_failures(pin_client):
    """Nach MAX_LOGIN_ATTEMPTS Fehlversuchen wird die IP fuer LOCKOUT_SECONDS gesperrt."""
    from app.api.auth import _login_attempts
    _login_attempts.clear()

    # 5 falsche Versuche -> 401, kein Lockout
    for i in range(5):
        r = await pin_client.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
        assert r.status_code == 401, f"Versuch {i + 1} sollte 401 sein"

    # 6. Versuch (selbst mit korrektem PIN!) -> 429 Lockout
    r = await pin_client.post("/api/auth/login", json={"pin": TEST_PIN})
    assert r.status_code == 429
    detail = r.json()["detail"].lower()
    assert "warten" in detail or "lockout" in detail

    _login_attempts.clear()


@pytest.mark.asyncio
async def test_pin_lockout_clears_on_success_before_lockout(pin_client):
    """4 Fehlversuche + 1 Erfolg loescht den Counter."""
    from app.api.auth import _login_attempts
    _login_attempts.clear()

    for _ in range(4):
        r = await pin_client.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
        assert r.status_code == 401

    # Korrekter PIN -> Erfolg, Counter weg
    r = await pin_client.post("/api/auth/login", json={"pin": TEST_PIN})
    assert r.status_code == 200
    assert len(_login_attempts) == 0
    _login_attempts.clear()


# --- Session-Ablauf, Backoff, SPA-Pfade ---


@pytest.mark.asyncio
async def test_expired_session_is_rejected(pin_client):
    from app.api import auth

    login = await pin_client.post("/api/auth/login", json={"pin": TEST_PIN})
    token = login.cookies["zw_session"]
    auth._sessions[token] = datetime.now(timezone.utc) - timedelta(seconds=1)
    pin_client.cookies.set("zw_session", token)
    resp = await pin_client.get("/api/documents")
    assert resp.status_code == 401
    assert token not in auth._sessions


@pytest.mark.asyncio
async def test_lockout_backoff_doubles_after_expired_lockout(pin_client):
    """S-MED1: nach Ablauf eines Lockouts fuehrt der naechste Fehlversuch zu
    einem doppelt so langen Lockout (Counter wird nicht zurueckgesetzt)."""
    from app.api import auth

    for _ in range(5):
        await pin_client.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
    ((ip, (count, lockout, last_seen)),) = auth._login_attempts.items()
    assert count == 5
    first = (lockout - last_seen).total_seconds()
    # Lockout kuenstlich ablaufen lassen
    auth._login_attempts[ip] = (count, datetime.now(timezone.utc) - timedelta(seconds=1), last_seen)
    r = await pin_client.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
    assert r.status_code == 401
    count2, lockout2, seen2 = auth._login_attempts[ip]
    assert count2 == 6
    assert (lockout2 - seen2).total_seconds() == pytest.approx(first * 2)


@pytest.mark.asyncio
async def test_spa_paths_not_blocked_by_pin(pin_client):
    """S-MED2: Nicht-/api-Pfade (SPA-Shell im Native-Betrieb) brauchen keine Session."""
    resp = await pin_client.get("/dokumente")
    assert resp.status_code != 401


# --- Eingabevalidierung ---


@pytest.mark.asyncio
async def test_non_ascii_pin_is_rejected_and_counted(pin_client):
    """Regression: compare_digest(str, str) warf TypeError bei Nicht-ASCII -> 500."""
    from app.api import auth

    resp = await pin_client.post("/api/auth/login", json={"pin": "ä"})
    assert resp.status_code == 401
    ((_, (count, _, _)),) = auth._login_attempts.items()
    assert count == 1


@pytest.mark.asyncio
async def test_empty_pin_is_validation_error(pin_client):
    resp = await pin_client.post("/api/auth/login", json={"pin": ""})
    assert resp.status_code == 422


def test_pin_enabled_with_empty_code_fails_fast():
    """Regression: PIN_ENABLED=true + leerer PIN_CODE akzeptierte den leeren PIN."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(PIN_ENABLED=True, PIN_CODE="")


# --- Trusted-Proxy-Handling (NEW-002) ---


def _client_for(app, host: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(host, 5000)), base_url="http://test")


@pytest.fixture
def lan_pin_app(pin_client):
    from app.main import app

    return app


@pytest.mark.asyncio
async def test_rotating_real_ip_from_lan_is_still_locked_out(lan_pin_app, monkeypatch):
    """Regression: ein LAN-Client (192.168.x) konnte per wechselndem X-Real-IP
    den Lockout umgehen und unbegrenzt PINs raten."""
    import app.config

    monkeypatch.setattr(app.config, "_in_container", lambda: False)
    async with _client_for(lan_pin_app, "192.168.1.50") as c:
        for i in range(5):
            r = await c.post("/api/auth/login", json={"pin": TEST_WRONG_PIN}, headers={"X-Real-IP": f"10.9.9.{i}"})
            assert r.status_code == 401
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN}, headers={"X-Real-IP": "10.9.9.99"})
        assert r.status_code == 429


@pytest.mark.asyncio
async def test_real_ip_honoured_only_from_docker_proxy(lan_pin_app, monkeypatch):
    """Docker: nginx (Compose-Netz) reicht die echte Client-IP weiter — der
    Lockout trifft den Client, nicht den Proxy."""
    import app.config
    from app.api import auth

    monkeypatch.setattr(app.config, "_in_container", lambda: True)
    async with _client_for(lan_pin_app, "172.18.0.3") as c:
        for _ in range(5):
            await c.post("/api/auth/login", json={"pin": TEST_WRONG_PIN}, headers={"X-Real-IP": "192.168.1.60"})
        assert set(auth._login_attempts) == {"192.168.1.60"}
        # Ein anderer Client hinter demselben Proxy ist nicht gesperrt.
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN}, headers={"X-Real-IP": "192.168.1.61"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_spoofed_forwarded_proto_from_lan_does_not_set_secure(lan_pin_app, monkeypatch):
    """R-07: X-Forwarded-Proto von einem Nicht-Proxy darf kein Secure-Cookie erzwingen."""
    import app.config

    monkeypatch.setattr(app.config, "_in_container", lambda: False)
    async with _client_for(lan_pin_app, "192.168.1.50") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN}, headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    assert "secure" not in r.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_forwarded_proto_from_docker_proxy_sets_secure(lan_pin_app, monkeypatch):
    import app.config

    monkeypatch.setattr(app.config, "_in_container", lambda: True)
    async with _client_for(lan_pin_app, "172.18.0.3") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN}, headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    assert "secure" in r.headers["set-cookie"].lower()


def test_login_attempt_table_is_bounded(monkeypatch):
    from app.api import auth

    auth._login_attempts.clear()
    monkeypatch.setattr(auth, "MAX_TRACKED_CLIENTS", 3)
    now = datetime.now(timezone.utc)
    for i in range(5):
        auth._record_failed_attempt(f"192.168.0.{i}", 1, now + timedelta(seconds=i))
    assert len(auth._login_attempts) == 3
    assert "192.168.0.0" not in auth._login_attempts  # am laengsten inaktiv
    auth._login_attempts.clear()


# --- AUTH-01: Counter-Vergessen + globales Fehlversuchs-Budget ---


@pytest.mark.asyncio
async def test_counter_survives_end_of_capped_lockout(pin_client):
    """AUTH-01: Endet ein gedeckelter Lockout (>= 1h), darf der Counter NICHT
    vergessen werden — frueher begann der Backoff danach wieder bei 0."""
    from app.api import auth

    now = datetime.now(timezone.utc)
    last_fail = now - timedelta(seconds=auth.MAX_LOCKOUT_SECONDS + 1)
    lockout_end = last_fail + timedelta(seconds=auth.MAX_LOCKOUT_SECONDS)  # = now - 1s
    auth._login_attempts["127.0.0.1"] = (12, lockout_end, last_fail)

    r = await pin_client.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
    assert r.status_code == 401
    count, lockout, seen = auth._login_attempts["127.0.0.1"]
    assert count == 13
    assert (lockout - seen).total_seconds() == pytest.approx(auth.MAX_LOCKOUT_SECONDS)


@pytest.mark.asyncio
async def test_counter_forgotten_after_long_idle_from_lockout_end(pin_client):
    from app.api import auth

    now = datetime.now(timezone.utc)
    lockout_end = now - timedelta(seconds=auth.ATTEMPT_FORGIVE_SECONDS + 1)
    auth._login_attempts["127.0.0.1"] = (12, lockout_end, lockout_end - timedelta(hours=1))

    r = await pin_client.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
    assert r.status_code == 401
    assert auth._login_attempts["127.0.0.1"][0] == 1


def test_forgive_window_is_much_longer_than_max_lockout():
    from app.api import auth

    assert auth.ATTEMPT_FORGIVE_SECONDS >= 12 * auth.MAX_LOCKOUT_SECONDS


@pytest.mark.asyncio
async def test_global_failure_budget_blocks_rotating_lan_ips(lan_pin_app, monkeypatch, db_session):
    """AUTH-01: viele LAN-IPs (IPv4-Aliase) duerfen den Per-IP-Lockout nicht
    parallelisieren — nach GLOBAL_MAX_FAILURES Fehlversuchen pro Stunde sperrt
    die Anmeldung fuer alle Nicht-Loopback-Clients, auch mit korrektem PIN."""
    import app.config
    from sqlalchemy import select

    from app.api import auth
    from app.models.notification import Notification

    monkeypatch.setattr(app.config, "_in_container", lambda: False)
    failures = 0
    ip = 0
    while failures < auth.GLOBAL_MAX_FAILURES:
        async with _client_for(lan_pin_app, f"192.168.5.{ip}") as c:
            for _ in range(auth.MAX_LOGIN_ATTEMPTS - 1):  # unter Per-IP-Lockout bleiben
                if failures >= auth.GLOBAL_MAX_FAILURES:
                    break
                r = await c.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
                assert r.status_code == 401
                failures += 1
        ip += 1

    async with _client_for(lan_pin_app, "192.168.5.250") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN})
    assert r.status_code == 429
    assert "netzwerk" in r.json()["detail"].lower()

    # Besitzer am Server-PC (Loopback) kommt weiterhin rein.
    async with _client_for(lan_pin_app, "127.0.0.1") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN})
    assert r.status_code == 200

    # Genau eine Warnung in der Glocke.
    rows = (await db_session.execute(select(Notification))).scalars().all()
    assert len([n for n in rows if "PIN" in n.title]) == 1


@pytest.mark.asyncio
async def test_global_budget_window_expires(lan_pin_app, monkeypatch):
    import app.config
    from app.api import auth

    monkeypatch.setattr(app.config, "_in_container", lambda: False)
    old = datetime.now(timezone.utc) - timedelta(seconds=auth.GLOBAL_FAILURE_WINDOW_SECONDS + 1)
    auth._global_failures.extend([old] * auth.GLOBAL_MAX_FAILURES)
    async with _client_for(lan_pin_app, "192.168.5.251") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN})
    assert r.status_code == 200
