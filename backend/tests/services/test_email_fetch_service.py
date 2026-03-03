import json
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.email_account import EmailAccount, ScheduleType
from app.models.processed_email import ProcessedEmail, EmailStatus
from app.models.processing_job import ProcessingJob, JobSource
from app.services.email_fetch_service import (
    parse_email_message,
    fetch_emails_for_account,
)


def _build_mime_email(
    subject="Rechnung",
    sender="firma@example.com",
    body="Anbei Ihre Rechnung.",
    attachments=None,
    message_id="<test123@example.com>",
):
    """Hilfsfunktion: baut eine MIME-E-Mail."""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 03 Mar 2026 10:00:00 +0100"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for name, content in (attachments or []):
        att = MIMEApplication(content, Name=name)
        att["Content-Disposition"] = f'attachment; filename="{name}"'
        msg.attach(att)
    return msg


def test_parse_email_message():
    msg = _build_mime_email(
        subject="Ihre Rechnung Nr. 123",
        sender="firma@test.de",
        body="Sehr geehrter Kunde, anbei Ihre Rechnung.",
        attachments=[("rechnung.pdf", b"%PDF-1.4 fake content")],
    )
    parsed = parse_email_message(msg.as_bytes())
    assert parsed["subject"] == "Ihre Rechnung Nr. 123"
    assert parsed["sender"] == "firma@test.de"
    assert "anbei Ihre Rechnung" in parsed["body"]
    assert parsed["message_id"] == "<test123@example.com>"
    assert len(parsed["attachments"]) == 1
    assert parsed["attachments"][0]["filename"] == "rechnung.pdf"


def test_parse_email_no_attachments():
    msg = _build_mime_email(body="Nur Text, keine Anhaenge.")
    parsed = parse_email_message(msg.as_bytes())
    assert len(parsed["attachments"]) == 0
    assert "Nur Text" in parsed["body"]


@pytest.mark.asyncio
async def test_fetch_emails_skips_already_processed(db_session, test_settings):
    account = EmailAccount(
        name="Test",
        imap_host="imap.test.com",
        username="user@test.com",
        encrypted_password="enc_pw",
        schedule_type=ScheduleType.MANUAL,
    )
    db_session.add(account)
    await db_session.flush()

    existing = ProcessedEmail(
        email_account_id=account.id,
        message_id="<already@test.com>",
        status=EmailStatus.IRRELEVANT,
    )
    db_session.add(existing)
    await db_session.commit()

    msg = _build_mime_email(message_id="<already@test.com>")

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1", msg.as_bytes())])
    mock_imap.__enter__ = MagicMock(return_value=mock_imap)
    mock_imap.__exit__ = MagicMock(return_value=False)

    with patch("app.services.email_fetch_service._connect_imap", return_value=mock_imap), \
         patch("app.services.email_fetch_service.decrypt_password", return_value="password"):
        stats = await fetch_emails_for_account(account, db_session, test_settings)

    assert stats["skipped"] == 1
    assert stats["relevant"] == 0


@pytest.mark.asyncio
async def test_fetch_emails_relevant_creates_job(db_session, test_settings):
    account = EmailAccount(
        name="Test",
        imap_host="imap.test.com",
        username="user@test.com",
        encrypted_password="enc_pw",
        schedule_type=ScheduleType.MANUAL,
    )
    db_session.add(account)
    await db_session.commit()

    msg = _build_mime_email(
        subject="Ihre Rechnung",
        attachments=[("rechnung.pdf", b"%PDF-1.4 fake")],
        message_id="<new@test.com>",
    )

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1", msg.as_bytes())])
    mock_imap.copy.return_value = ("OK", [])
    mock_imap.store.return_value = ("OK", [])
    mock_imap.__enter__ = MagicMock(return_value=mock_imap)
    mock_imap.__exit__ = MagicMock(return_value=False)

    relevance_result = {"relevant": True, "reason": "Rechnung"}

    with patch("app.services.email_fetch_service._connect_imap", return_value=mock_imap), \
         patch("app.services.email_fetch_service.decrypt_password", return_value="password"), \
         patch("app.services.email_fetch_service.check_email_relevance", new_callable=AsyncMock, return_value=relevance_result):
        stats = await fetch_emails_for_account(account, db_session, test_settings)

    assert stats["relevant"] == 1

    result = await db_session.execute(select(ProcessingJob).where(ProcessingJob.source == JobSource.EMAIL))
    jobs = result.scalars().all()
    assert len(jobs) >= 1

    result = await db_session.execute(select(ProcessedEmail))
    processed = result.scalars().all()
    assert len(processed) == 1
    assert processed[0].status == EmailStatus.RELEVANT


@pytest.mark.asyncio
async def test_fetch_emails_irrelevant(db_session, test_settings):
    account = EmailAccount(
        name="Test",
        imap_host="imap.test.com",
        username="user@test.com",
        encrypted_password="enc_pw",
        schedule_type=ScheduleType.MANUAL,
    )
    db_session.add(account)
    await db_session.commit()

    msg = _build_mime_email(
        subject="Newsletter",
        body="Unsere Angebote...",
        message_id="<newsletter@test.com>",
    )

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1", msg.as_bytes())])
    mock_imap.copy.return_value = ("OK", [])
    mock_imap.store.return_value = ("OK", [])

    relevance_result = {"relevant": False, "reason": "Newsletter"}

    with patch("app.services.email_fetch_service._connect_imap", return_value=mock_imap), \
         patch("app.services.email_fetch_service.decrypt_password", return_value="password"), \
         patch("app.services.email_fetch_service.check_email_relevance", new_callable=AsyncMock, return_value=relevance_result):
        stats = await fetch_emails_for_account(account, db_session, test_settings)

    assert stats["irrelevant"] == 1
    assert stats["relevant"] == 0

    result = await db_session.execute(select(ProcessedEmail))
    processed = result.scalars().all()
    assert len(processed) == 1
    assert processed[0].status == EmailStatus.IRRELEVANT


@pytest.mark.asyncio
async def test_fetch_emails_no_unseen(db_session, test_settings):
    account = EmailAccount(
        name="Test",
        imap_host="imap.test.com",
        username="user@test.com",
        encrypted_password="enc_pw",
    )
    db_session.add(account)
    await db_session.commit()

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"0"])
    mock_imap.search.return_value = ("OK", [b""])
    mock_imap.close.return_value = ("OK", [])
    mock_imap.logout.return_value = ("OK", [])

    with patch("app.services.email_fetch_service._connect_imap", return_value=mock_imap), \
         patch("app.services.email_fetch_service.decrypt_password", return_value="password"):
        stats = await fetch_emails_for_account(account, db_session, test_settings)

    assert stats["total"] == 0
