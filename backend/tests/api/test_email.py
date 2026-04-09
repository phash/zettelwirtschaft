import pytest
from unittest.mock import AsyncMock, patch

from app.config import Settings, get_settings
from app.main import app

TEST_ENCRYPTION_KEY = "dGVzdGtleTE2Ynl0ZXNrZXk9"


def _mock_settings_with_key(**kwargs):
    """Erstellt Mock-Settings mit gesetztem EMAIL_ENCRYPTION_KEY."""
    return Settings(EMAIL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY, **kwargs)


@pytest.fixture
def email_client_settings(test_settings):
    """Ueberschreibt get_settings mit EMAIL_ENCRYPTION_KEY fuer E-Mail-Tests."""
    s = Settings(
        EMAIL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY,
        DATABASE_URL=test_settings.DATABASE_URL,
        UPLOAD_DIR=test_settings.UPLOAD_DIR,
        WATCH_DIR=test_settings.WATCH_DIR,
        ARCHIVE_DIR=test_settings.ARCHIVE_DIR,
        THUMBNAIL_DIR=test_settings.THUMBNAIL_DIR,
        OLLAMA_BASE_URL=test_settings.OLLAMA_BASE_URL,
        LOG_LEVEL=test_settings.LOG_LEVEL,
    )
    app.dependency_overrides[get_settings] = lambda: s
    yield s
    app.dependency_overrides[get_settings] = lambda: test_settings


async def _create_account(client, email_client_settings):
    """Hilfsfunktion: Erstellt ein E-Mail-Konto mit encrypt_password Mock."""
    with patch("app.api.email.encrypt_password", return_value="enc"):
        resp = await client.post("/api/email/accounts", json={
            "name": "Test",
            "imap_host": "imap.test.com",
            "username": "user@test.com",
            "password": "pw",
        })
    return resp


@pytest.mark.asyncio
async def test_list_accounts_empty(client):
    resp = await client.get("/api/email/accounts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_account_requires_encryption_key(client):
    """Ohne EMAIL_ENCRYPTION_KEY gibt create_account 503 zurueck."""
    resp = await client.post("/api/email/accounts", json={
        "name": "Test",
        "imap_host": "imap.test.com",
        "username": "user@test.com",
        "password": "pw",
    })
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_create_account(client, email_client_settings):
    with patch("app.api.email.encrypt_password", return_value="encrypted_pw"):
        resp = await client.post("/api/email/accounts", json={
            "name": "Gmail Privat",
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "use_ssl": True,
            "username": "user@gmail.com",
            "password": "app-password",
            "schedule_type": "MANUAL",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Gmail Privat"
    assert data["imap_host"] == "imap.gmail.com"
    assert "password" not in data


@pytest.mark.asyncio
async def test_create_and_list_accounts(client, email_client_settings):
    await _create_account(client, email_client_settings)
    resp = await client.get("/api/email/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_delete_account(client, email_client_settings):
    create_resp = await _create_account(client, email_client_settings)
    account_id = create_resp.json()["id"]
    del_resp = await client.delete(f"/api/email/accounts/{account_id}")
    assert del_resp.status_code == 204

    list_resp = await client.get("/api/email/accounts")
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_test_connection(client, email_client_settings):
    create_resp = await _create_account(client, email_client_settings)
    account_id = create_resp.json()["id"]

    with patch("app.api.email.test_imap_connection", new_callable=AsyncMock, return_value={"success": True, "error": None}):
        resp = await client.post(f"/api/email/accounts/{account_id}/test")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_manual_fetch(client, email_client_settings):
    create_resp = await _create_account(client, email_client_settings)
    account_id = create_resp.json()["id"]

    mock_stats = {"total": 5, "relevant": 2, "irrelevant": 3, "skipped": 0, "failed": 0}
    with patch("app.api.email.fetch_emails_for_account", new_callable=AsyncMock, return_value=mock_stats):
        resp = await client.post(f"/api/email/accounts/{account_id}/fetch")
    assert resp.status_code == 200
    assert resp.json()["relevant"] == 2


@pytest.mark.asyncio
async def test_get_history_not_found(client):
    resp = await client.get("/api/email/accounts/9999/history")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_stats_empty(client):
    resp = await client.get("/api/email/stats")
    assert resp.status_code == 200
    assert resp.json() == []
