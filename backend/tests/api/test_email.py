import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_list_accounts_empty(client):
    resp = await client.get("/api/email/accounts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_account(client):
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleTE2Ynl0ZXNrZXk9") as mock_gen, \
         patch("app.api.email.encrypt_password", return_value="encrypted_pw"):
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
async def test_create_and_list_accounts(client):
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleTE2Ynl0ZXNrZXk9"), \
         patch("app.api.email.encrypt_password", return_value="enc"):
        await client.post("/api/email/accounts", json={
            "name": "Test",
            "imap_host": "imap.test.com",
            "username": "user@test.com",
            "password": "pw",
        })
    resp = await client.get("/api/email/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_delete_account(client):
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleTE2Ynl0ZXNrZXk9"), \
         patch("app.api.email.encrypt_password", return_value="enc"):
        create_resp = await client.post("/api/email/accounts", json={
            "name": "ToDelete",
            "imap_host": "imap.test.com",
            "username": "user@test.com",
            "password": "pw",
        })
    account_id = create_resp.json()["id"]
    del_resp = await client.delete(f"/api/email/accounts/{account_id}")
    assert del_resp.status_code == 204

    list_resp = await client.get("/api/email/accounts")
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_test_connection(client):
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleTE2Ynl0ZXNrZXk9"), \
         patch("app.api.email.encrypt_password", return_value="enc"):
        create_resp = await client.post("/api/email/accounts", json={
            "name": "Test",
            "imap_host": "imap.test.com",
            "username": "user@test.com",
            "password": "pw",
        })
    account_id = create_resp.json()["id"]

    with patch("app.api.email.test_imap_connection", new_callable=AsyncMock, return_value={"success": True, "error": None}):
        resp = await client.post(f"/api/email/accounts/{account_id}/test")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_manual_fetch(client):
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleTE2Ynl0ZXNrZXk9"), \
         patch("app.api.email.encrypt_password", return_value="enc"):
        create_resp = await client.post("/api/email/accounts", json={
            "name": "Test",
            "imap_host": "imap.test.com",
            "username": "user@test.com",
            "password": "pw",
        })
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
