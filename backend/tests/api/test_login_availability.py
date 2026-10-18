"""AVAIL-04 / AVAIL-07: Verfuegbarkeit des PIN-Logins.

AVAIL-04: Hinter Docker Desktop teilen sich alle Geraete den Gateway-Schluessel
— der Per-Key-Backoff darf den Haushalt dann nicht bis zu 1 h aussperren.
AVAIL-07: ``_login_attempts`` bleibt auch dann begrenzt, wenn alle Eintraege
gesperrt sind.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import auth
from tests.conftest import make_test_client
from tests.credentials import TEST_PIN, TEST_WRONG_PIN


@pytest.fixture(autouse=True)
def _clean_login_state():
    auth.reset_login_state()
    yield
    auth.reset_login_state()


@pytest.fixture
async def pin_app(test_settings, test_engine, test_session_factory):
    settings = test_settings.model_copy(update={"PIN_ENABLED": True, "PIN_CODE": TEST_PIN})
    async with make_test_client(settings, test_session_factory):
        from app.main import app

        yield app


def _client_for(app, host: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(host, 5000)), base_url="http://test")


@pytest.mark.asyncio
async def test_shared_gateway_key_lockout_is_capped(pin_app, monkeypatch):
    """Docker-Betrieb, nginx meldet die Gateway-Adresse als Client: nach vielen
    Fehlversuchen bleibt die Sperre kurz (statt bis 1 h)."""
    import app.config

    monkeypatch.setattr(app.config, "_in_container", lambda: True)
    monkeypatch.setattr(auth, "GLOBAL_MAX_FAILURES", 10_000)  # nur Per-Key-Logik pruefen
    gateway = "172.18.0.1"
    now = datetime.now(timezone.utc)
    # Ausgangslage: viele Fehlversuche, letzte Sperre gerade abgelaufen.
    auth._login_attempts[gateway] = (20, now - timedelta(seconds=1), now - timedelta(seconds=61))
    async with _client_for(pin_app, "172.18.0.3") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_WRONG_PIN}, headers={"X-Real-IP": gateway})
    assert r.status_code == 401
    _count, lockout, _seen = auth._login_attempts[gateway]
    assert lockout is not None
    assert (lockout - datetime.now(timezone.utc)).total_seconds() <= auth.SHARED_KEY_MAX_LOCKOUT_SECONDS


@pytest.mark.asyncio
async def test_real_lan_client_keeps_full_backoff(pin_app, monkeypatch):
    import app.config

    monkeypatch.setattr(app.config, "_in_container", lambda: True)
    monkeypatch.setattr(auth, "GLOBAL_MAX_FAILURES", 10_000)
    ip = "192.168.178.20"
    now = datetime.now(timezone.utc)
    auth._login_attempts[ip] = (20, now - timedelta(seconds=1), now - timedelta(seconds=61))
    async with _client_for(pin_app, "172.18.0.3") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_WRONG_PIN}, headers={"X-Real-IP": ip})
    assert r.status_code == 401
    _count, lockout, _seen = auth._login_attempts[ip]
    remaining = (lockout - datetime.now(timezone.utc)).total_seconds()
    assert remaining > auth.SHARED_KEY_MAX_LOCKOUT_SECONDS


def test_table_never_grows_beyond_cap_when_all_locked(monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED_CLIENTS", 3)
    now = datetime.now(timezone.utc)
    for i in range(3):
        assert auth._record_failed_attempt(f"10.0.0.{i}", auth.MAX_LOGIN_ATTEMPTS, now)
    # alle drei gesperrt -> neue Adressen werden nicht mehr aufgenommen
    for i in range(3, 50):
        assert auth._record_failed_attempt(f"10.0.0.{i}", 1, now) is False
    assert len(auth._login_attempts) == 3
    # gesperrte Eintraege wurden NICHT verdraengt (kein Sperr-Reset per Adressflut)
    assert set(auth._login_attempts) == {"10.0.0.0", "10.0.0.1", "10.0.0.2"}


@pytest.mark.asyncio
async def test_full_locked_table_fails_closed_but_correct_pin_works(pin_app, monkeypatch):
    monkeypatch.setattr(auth, "MAX_TRACKED_CLIENTS", 2)
    monkeypatch.setattr(auth, "GLOBAL_MAX_FAILURES", 10_000)
    now = datetime.now(timezone.utc)
    for i in range(2):
        auth._record_failed_attempt(f"10.0.0.{i}", auth.MAX_LOGIN_ATTEMPTS, now)
    async with _client_for(pin_app, "10.0.0.77") as c:
        r = await c.post("/api/auth/login", json={"pin": TEST_WRONG_PIN})
        assert r.status_code == 429
        assert "10.0.0.77" not in auth._login_attempts
        r = await c.post("/api/auth/login", json={"pin": TEST_PIN})
        assert r.status_code == 200
    assert len(auth._login_attempts) == 2


def test_session_cleanup_is_throttled_but_expiry_exact(monkeypatch):

    now = datetime.now(timezone.utc)
    auth._sessions["valid"] = now + timedelta(minutes=5)
    auth._sessions["expired"] = now - timedelta(seconds=1)
    try:
        assert auth.is_session_valid("valid")
        # Abgelaufene Session ist sofort ungueltig, auch ohne Aufraeum-Lauf.
        auth._sessions["valid2"] = now - timedelta(seconds=1)
        assert not auth.is_session_valid("valid2")

        sweeps = []
        original_items = auth._login_attempts.items

        class _Spy(dict):
            def items(self):
                sweeps.append(1)
                return original_items()

        monkeypatch.setattr(auth, "_login_attempts", _Spy(auth._login_attempts))
        for _ in range(100):
            auth.is_session_valid("valid")
        assert len(sweeps) == 0, "Aufraeumen lief trotz Intervall bei jedem Request"
    finally:
        auth._sessions.clear()
