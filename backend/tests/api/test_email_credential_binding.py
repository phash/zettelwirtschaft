"""SEC-DATA-02: Das gespeicherte IMAP-Passwort darf nicht auf einen anderen
Server/Benutzer umgebogen werden, ohne es neu einzugeben."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models.email_account import EmailAccount
from app.models.notification import Notification
from app.services.crypto_service import decrypt_password
from tests.credentials import TEST_EMAIL_ENCRYPTION_KEY, TEST_IMAP_PASSWORD, TEST_IMAP_PASSWORD_2

ACCOUNT = {"name": "GMX", "imap_host": "imap.gmx.net", "username": "victim@gmx.de", "password": TEST_IMAP_PASSWORD}


@pytest.fixture(autouse=True)
def _email_key(client, test_settings):
    s = test_settings.model_copy(update={"EMAIL_ENCRYPTION_KEY": TEST_EMAIL_ENCRYPTION_KEY})
    app.dependency_overrides[get_settings] = lambda: s
    yield
    app.dependency_overrides[get_settings] = lambda: test_settings


async def _create(client) -> int:
    resp = await client.post("/api/email/accounts", json=ACCOUNT)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"imap_host": "192.168.1.66"},
        {"imap_port": 143},
        {"use_ssl": False},
        {"username": "attacker@evil.example"},
        {"imap_host": "127.0.0.1", "imap_port": 1143, "use_ssl": False},
    ],
)
async def test_retarget_without_password_is_rejected(client, db_session, change):
    account_id = await _create(client)

    resp = await client.put(f"/api/email/accounts/{account_id}", json=change)

    assert resp.status_code == 422
    assert "Passwort" in resp.json()["detail"]
    account = await db_session.get(EmailAccount, account_id)
    await db_session.refresh(account)
    assert (account.imap_host, account.imap_port, account.use_ssl, account.username) == (
        "imap.gmx.net", 993, True, "victim@gmx.de",
    )


@pytest.mark.asyncio
async def test_retarget_with_new_password_is_allowed_and_notified(client, db_session):
    account_id = await _create(client)

    resp = await client.put(
        f"/api/email/accounts/{account_id}",
        json={"imap_host": "imap.web.de", "password": TEST_IMAP_PASSWORD_2},
    )

    assert resp.status_code == 200, resp.text
    account = await db_session.get(EmailAccount, account_id)
    await db_session.refresh(account)
    assert account.imap_host == "imap.web.de"
    assert decrypt_password(account.encrypted_password, TEST_EMAIL_ENCRYPTION_KEY) == TEST_IMAP_PASSWORD_2
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert any("imap.gmx.net" in n.message and "imap.web.de" in n.message for n in notes)


@pytest.mark.asyncio
async def test_unchanged_values_and_other_fields_need_no_password(client):
    """Die SPA schickt beim Bearbeiten alle Felder mit — unveraenderte Werte
    duerfen kein Passwort erzwingen."""
    account_id = await _create(client)

    resp = await client.put(
        f"/api/email/accounts/{account_id}",
        json={
            "name": "GMX privat",
            "imap_host": "imap.gmx.net",
            "imap_port": 993,
            "use_ssl": True,
            "username": "victim@gmx.de",
            "folder_inbox": "INBOX",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "GMX privat"
