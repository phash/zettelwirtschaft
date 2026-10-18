"""Gemeinsame Fixtures der E-Mail-Abruf-Tests (Fake-IMAP-Server).

Als Plugin in ``tests/conftest.py`` registriert (``pytest_plugins``), damit
Testmodule die Fixtures nicht aus anderen Testmodulen importieren muessen.
"""

import pytest

from app.config import Settings
from app.models.email_account import EmailAccount, ScheduleType
from app.services.crypto_service import encrypt_password
from tests.credentials import TEST_EMAIL_ENCRYPTION_KEY, TEST_IMAP_PASSWORD


@pytest.fixture
def fernet_key() -> str:
    return TEST_EMAIL_ENCRYPTION_KEY


@pytest.fixture
def mail_settings(test_settings, fernet_key) -> Settings:
    return test_settings.model_copy(update={"EMAIL_ENCRYPTION_KEY": fernet_key, "IMAP_TIMEOUT": 2})


@pytest.fixture
async def account_id(db_session, fernet_key) -> int:
    account = EmailAccount(
        name="Test",
        imap_host="imap.test.com",
        username="user@test.com",
        encrypted_password=encrypt_password(TEST_IMAP_PASSWORD, fernet_key),  # echte Verschluesselung
        schedule_type=ScheduleType.MANUAL,
    )
    db_session.add(account)
    await db_session.commit()
    return account.id
