import pytest
from unittest.mock import AsyncMock, patch

from app.config import get_settings
from app.main import app
from app.models.email_account import EmailAccount
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.services.crypto_service import decrypt_password
from app.services.task_service import shutdown_tasks, wait_for_task
from tests.credentials import TEST_EMAIL_ENCRYPTION_KEY, TEST_IMAP_PASSWORD, TEST_IMAP_PASSWORD_2


@pytest.fixture(autouse=True)
async def _cancel_leftover_tasks():
    yield
    await shutdown_tasks()


@pytest.fixture
def email_client_settings(test_settings):
    """Ueberschreibt get_settings mit EMAIL_ENCRYPTION_KEY fuer E-Mail-Tests."""
    s = test_settings.model_copy(update={"EMAIL_ENCRYPTION_KEY": TEST_EMAIL_ENCRYPTION_KEY})
    app.dependency_overrides[get_settings] = lambda: s
    yield s
    app.dependency_overrides[get_settings] = lambda: test_settings


_ACCOUNT = {
    "name": "Test",
    "imap_host": "imap.test.com",
    "username": "user@test.com",
    "password": TEST_IMAP_PASSWORD,
}


async def _create_account(client, **overrides):
    resp = await client.post("/api/email/accounts", json={**_ACCOUNT, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_list_accounts_empty(client):
    resp = await client.get("/api/email/accounts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_account_requires_encryption_key(client):
    """Ohne EMAIL_ENCRYPTION_KEY gibt create_account 503 zurueck — mit Hinweis fuer beide Modi."""
    resp = await client.post("/api/email/accounts", json=_ACCOUNT)
    assert resp.status_code == 503
    assert ".env" in resp.json()["detail"] and "config.toml" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_account_encrypts_password(client, email_client_settings, db_session):
    data = await _create_account(client, name="Gmail Privat", imap_host="imap.gmail.com",
                                 password=TEST_IMAP_PASSWORD, schedule_type="MANUAL")
    assert data["name"] == "Gmail Privat"
    assert data["schedule_type"] == "MANUAL"
    assert "password" not in data and "encrypted_password" not in data
    account = await db_session.get(EmailAccount, data["id"])
    assert decrypt_password(account.encrypted_password, TEST_EMAIL_ENCRYPTION_KEY) == TEST_IMAP_PASSWORD


@pytest.mark.asyncio
async def test_create_and_list_accounts(client, email_client_settings):
    await _create_account(client)
    resp = await client.get("/api/email/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"schedule_type": "STÜNDLICH"},
    {"schedule_type": "CRON"},                               # CRON ohne Ausdruck
    {"schedule_type": "CRON", "cron_expression": "jeden Morgen"},
    {"schedule_type": "CRON", "cron_expression": "61 * * * *"},
    {"imap_port": 0},
    {"name": ""},
], ids=["bad-type", "cron-missing", "cron-garbage", "cron-range", "port-0", "empty-name"])
async def test_create_account_validation_returns_422(client, email_client_settings, overrides):
    resp = await client.post("/api/email/accounts", json={**_ACCOUNT, **overrides})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_account_unknown_scope_returns_422(client, email_client_settings):
    resp = await client.post("/api/email/accounts", json={**_ACCOUNT, "filing_scope_id": "gibt-es-nicht"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Ablagebereich nicht gefunden"


@pytest.mark.asyncio
async def test_create_cron_account_normalises_expression(client, email_client_settings):
    data = await _create_account(client, schedule_type="CRON", cron_expression="  0  8 * * * ")
    assert data["cron_expression"] == "0 8 * * *"


@pytest.mark.asyncio
async def test_update_account(client, email_client_settings, db_session):
    account_id = (await _create_account(client))["id"]
    resp = await client.put(f"/api/email/accounts/{account_id}", json={
        "name": "Neu", "schedule_type": "CRON", "cron_expression": "*/15 * * * *",
        "password": TEST_IMAP_PASSWORD_2, "is_active": False,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert (data["name"], data["schedule_type"], data["is_active"]) == ("Neu", "CRON", False)
    account = await db_session.get(EmailAccount, account_id)
    assert decrypt_password(account.encrypted_password, TEST_EMAIL_ENCRYPTION_KEY) == TEST_IMAP_PASSWORD_2


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"name": None},
    {"imap_port": None},
    {"imap_port": 0},
    {"name": ""},
    {"schedule_type": "UNSINN"},
    {"schedule_type": "CRON"},                     # Konto hat noch keinen Ausdruck
    {"cron_expression": "kaputt"},
    {"filing_scope_id": "gibt-es-nicht"},
], ids=["null-name", "null-port", "port-0", "empty-name", "bad-type", "cron-missing", "cron-bad", "scope"])
async def test_update_account_validation_returns_422(client, email_client_settings, payload):
    account_id = (await _create_account(client))["id"]
    resp = await client.put(f"/api/email/accounts/{account_id}", json=payload)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_update_cannot_remove_cron_expression_of_cron_account(client, email_client_settings):
    account_id = (await _create_account(client, schedule_type="CRON", cron_expression="0 8 * * *"))["id"]
    resp = await client.put(f"/api/email/accounts/{account_id}", json={"cron_expression": ""})
    assert resp.status_code == 422
    resp = await client.put(f"/api/email/accounts/{account_id}",
                            json={"schedule_type": "MANUAL", "cron_expression": None})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_account(client, email_client_settings):
    account_id = (await _create_account(client))["id"]
    del_resp = await client.delete(f"/api/email/accounts/{account_id}")
    assert del_resp.status_code == 204

    list_resp = await client.get("/api/email/accounts")
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_test_connection(client, email_client_settings):
    account_id = (await _create_account(client))["id"]

    result = {"success": True, "error": None, "warning": "Zielordner fehlt"}
    with patch("app.api.email.test_imap_connection", new_callable=AsyncMock, return_value=result):
        resp = await client.post(f"/api/email/accounts/{account_id}/test")
    assert resp.status_code == 200
    assert resp.json() == result


@pytest.mark.asyncio
async def test_manual_fetch_runs_as_background_task(client, email_client_settings):
    account_id = (await _create_account(client))["id"]

    stats = {"total": 5, "relevant": 2, "irrelevant": 3, "skipped": 0, "failed": 0,
             "remaining": 0, "error": None}
    with patch("app.api.email.fetch_emails_for_account", new_callable=AsyncMock, return_value=stats) as fetch:
        resp = await client.post(f"/api/email/accounts/{account_id}/fetch")
        assert resp.status_code == 202
        task = resp.json()
        assert (task["kind"], task["key"]) == ("email_fetch", str(account_id))
        await wait_for_task(task["id"], timeout=5)

    assert fetch.await_args.args[0] == account_id
    done = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert done["status"] == "SUCCEEDED"
    assert done["result"] == stats


@pytest.mark.asyncio
async def test_manual_fetch_connection_error_fails_task(client, email_client_settings):
    """Verbindungsfehler darf nicht als Erfolg mit 0 E-Mails gemeldet werden."""
    account_id = (await _create_account(client))["id"]
    stats = {"total": 0, "relevant": 0, "irrelevant": 0, "skipped": 0, "failed": 0,
             "remaining": 0, "error": "Server nicht gefunden — Hostname prüfen."}
    with patch("app.api.email.fetch_emails_for_account", new_callable=AsyncMock, return_value=stats):
        task = (await client.post(f"/api/email/accounts/{account_id}/fetch")).json()
        await wait_for_task(task["id"], timeout=5)

    done = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert done["status"] == "FAILED"
    assert done["error"] == "Server nicht gefunden — Hostname prüfen."


@pytest.mark.asyncio
async def test_manual_fetch_unknown_account_returns_404(client, email_client_settings):
    resp = await client.post("/api/email/accounts/9999/fetch")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_email_settings_info(client, email_client_settings):
    resp = await client.get("/api/email/settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "timezone": "Europe/Berlin", "unseen_only": True, "max_per_run": 25, "idle_interval_minutes": 5,
    }


@pytest.mark.asyncio
async def test_get_history_not_found(client):
    resp = await client.get("/api/email/accounts/9999/history")
    assert resp.status_code == 404


async def _add_processed(db_session, account_id):
    for i, status in enumerate([EmailStatus.RELEVANT, EmailStatus.IRRELEVANT, EmailStatus.FAILED,
                                EmailStatus.IRRELEVANT]):
        db_session.add(ProcessedEmail(email_account_id=account_id, message_id=f"<m{i}@t>",
                                      subject=f"Mail {i}", status=status))
    await db_session.commit()


@pytest.mark.asyncio
async def test_get_history_with_data(client, email_client_settings, db_session):
    account_id = (await _create_account(client))["id"]
    await _add_processed(db_session, account_id)

    resp = await client.get(f"/api/email/accounts/{account_id}/history", params={"limit": 2})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert items[0]["subject"] == "Mail 3" and items[0]["status"] == "IRRELEVANT"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [-1, 0, 201])
async def test_get_history_rejects_out_of_range_limit(client, email_client_settings, limit):
    """SQLite behandelt LIMIT -1 als unbegrenzt — darf le=200 nicht umgehen."""
    account_id = (await _create_account(client))["id"]
    resp = await client.get(f"/api/email/accounts/{account_id}/history", params={"limit": limit})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_stats_empty(client):
    resp = await client.get("/api/email/stats")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_stats_with_data(client, email_client_settings, db_session):
    account_id = (await _create_account(client))["id"]
    await _add_processed(db_session, account_id)

    [row] = (await client.get("/api/email/stats")).json()
    assert row["account_id"] == account_id
    assert (row["total_processed"], row["relevant"], row["irrelevant"], row["failed"]) == (4, 1, 2, 1)
