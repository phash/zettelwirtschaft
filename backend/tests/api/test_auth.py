from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import clear_all_sessions
from app.config import Settings


@pytest.fixture
def pin_settings(test_settings: Settings) -> Settings:
    """Settings with PIN enabled."""
    return Settings(
        DATABASE_URL=test_settings.DATABASE_URL,
        UPLOAD_DIR=test_settings.UPLOAD_DIR,
        WATCH_DIR=test_settings.WATCH_DIR,
        ARCHIVE_DIR=test_settings.ARCHIVE_DIR,
        THUMBNAIL_DIR=test_settings.THUMBNAIL_DIR,
        OLLAMA_BASE_URL=test_settings.OLLAMA_BASE_URL,
        LOG_LEVEL="DEBUG",
        PIN_ENABLED=True,
        PIN_CODE="1234",
        PIN_SESSION_TIMEOUT_MINUTES=60,
    )


@pytest.fixture
async def pin_client(pin_settings, test_engine, test_session_factory):
    from app.config import get_settings
    from app.main import app

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: pin_settings

    clear_all_sessions()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    clear_all_sessions()


from app.database import get_db


# --- Tests with PIN disabled (default) ---


@pytest.mark.asyncio
async def test_auth_status_pin_disabled(client):
    """When PIN is disabled, status returns not enabled + authenticated."""
    resp = await client.get("/api/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pin_enabled"] is False
    assert data["authenticated"] is True


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


@pytest.mark.asyncio
async def test_login_correct_pin(pin_client):
    """Login with correct PIN succeeds and sets cookie."""
    resp = await pin_client.post("/api/auth/login", json={"pin": "1234"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "zw_session" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_pin(pin_client):
    """Login with wrong PIN fails."""
    resp = await pin_client.post("/api/auth/login", json={"pin": "9999"})
    assert resp.status_code == 200
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
    login_resp = await pin_client.post("/api/auth/login", json={"pin": "1234"})
    assert login_resp.json()["success"] is True

    # Set cookie for subsequent requests
    pin_client.cookies.set("zw_session", login_resp.cookies["zw_session"])

    # Access protected endpoint
    resp = await pin_client.get("/api/auth/status")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True

    # Logout
    logout_resp = await pin_client.post("/api/auth/logout")
    assert logout_resp.status_code == 200

    # Clear cookie
    pin_client.cookies.clear()

    # Verify blocked again
    resp = await pin_client.get("/api/documents")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_when_pin_disabled(client):
    """Login when PIN is disabled always succeeds."""
    resp = await client.post("/api/auth/login", json={"pin": "anything"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
