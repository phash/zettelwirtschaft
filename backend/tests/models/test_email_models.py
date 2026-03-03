import pytest
from sqlalchemy import select

from app.models.email_account import EmailAccount, ScheduleType
from app.models.processed_email import ProcessedEmail, EmailStatus
from app.models.processing_job import JobSource


@pytest.mark.asyncio
async def test_create_email_account(db_session):
    account = EmailAccount(
        name="Test Gmail",
        imap_host="imap.gmail.com",
        imap_port=993,
        use_ssl=True,
        username="test@gmail.com",
        encrypted_password="encrypted_data_here",
        schedule_type=ScheduleType.MANUAL,
    )
    db_session.add(account)
    await db_session.flush()

    result = await db_session.execute(select(EmailAccount))
    saved = result.scalar_one()
    assert saved.name == "Test Gmail"
    assert saved.imap_host == "imap.gmail.com"
    assert saved.schedule_type == ScheduleType.MANUAL
    assert saved.is_active is True
    assert saved.folder_inbox == "INBOX"


@pytest.mark.asyncio
async def test_create_processed_email(db_session):
    account = EmailAccount(
        name="Test",
        imap_host="imap.test.com",
        username="user@test.com",
        encrypted_password="enc",
    )
    db_session.add(account)
    await db_session.flush()

    processed = ProcessedEmail(
        email_account_id=account.id,
        message_id="<abc123@test.com>",
        subject="Rechnung",
        sender="firma@example.com",
        status=EmailStatus.RELEVANT,
    )
    db_session.add(processed)
    await db_session.flush()

    result = await db_session.execute(select(ProcessedEmail))
    saved = result.scalar_one()
    assert saved.message_id == "<abc123@test.com>"
    assert saved.status == EmailStatus.RELEVANT


def test_job_source_email():
    assert JobSource.EMAIL.value == "EMAIL"
