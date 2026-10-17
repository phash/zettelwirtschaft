"""AVAIL-02: praeparierte Mails duerfen den Server nicht blockieren.

- MIME-/HTML-Parsing laeuft nicht auf dem Event-Loop.
- Obergrenzen fuer Anhaenge (je Anhang ein OCR+LLM-Job), MIME-Teile, HTML- und
  Body-Groesse.
"""

import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select

from app.models.processing_job import JobSource, ProcessingJob
from app.services import email_fetch_service as efs
from app.services.email_fetch_service import parse_email_message
from tests.services.fake_imap import FakeImapServer
from tests.services.test_email_fetch_service import (  # noqa: F401 — Fixtures
    PDF,
    _build_mime_email,
    _records,
    _run,
    account_id,
    fernet_key,
    mail_settings,
)


def test_attachment_count_is_capped():
    atts = [(f"r{i}.pdf", PDF) for i in range(efs._MAX_ATTACHMENTS_PER_EMAIL + 15)]
    parsed = parse_email_message(_build_mime_email(attachments=atts).as_bytes())
    assert len(parsed["attachments"]) == efs._MAX_ATTACHMENTS_PER_EMAIL
    assert parsed["skipped_attachments"] == 15


def test_mime_part_count_is_capped(monkeypatch):
    monkeypatch.setattr(efs, "_MAX_MIME_PARTS", 10)
    monkeypatch.setattr(efs, "_MAX_ATTACHMENTS_PER_EMAIL", 1000)
    atts = [(f"r{i}.pdf", PDF) for i in range(50)]
    parsed = parse_email_message(_build_mime_email(attachments=atts).as_bytes())
    # 1 Text-Teil + 9 Anhaenge, danach Abbruch
    assert len(parsed["attachments"]) == 9


def test_html_and_body_size_are_capped(monkeypatch):
    monkeypatch.setattr(efs, "_MAX_HTML_CHARS", 10_000)
    monkeypatch.setattr(efs, "_MAX_BODY_CHARS", 5_000)
    msg = MIMEMultipart()
    msg["Message-ID"] = "<html@t>"
    msg.attach(MIMEText("<p>" + "x" * 200_000 + "</p>", "html", "utf-8"))
    parsed = parse_email_message(msg.as_bytes())
    assert 0 < len(parsed["body"]) <= 5_000


def test_html_to_text_truncates_before_parsing(monkeypatch):
    monkeypatch.setattr(efs, "_MAX_HTML_CHARS", 100)
    fed: list[int] = []
    real_feed = efs._HtmlTextExtractor.feed

    def spy(self, data):
        fed.append(len(data))
        return real_feed(self, data)

    monkeypatch.setattr(efs._HtmlTextExtractor, "feed", spy)
    efs._html_to_text("<p>" + "y" * 10_000 + "</p>")
    assert fed == [100]


async def test_parsing_runs_off_the_event_loop(account_id, test_session_factory, mail_settings, monkeypatch):
    threads: list[str] = []
    real_parse = efs.parse_email_message

    def spy(raw):
        threads.append(threading.current_thread().name)
        return real_parse(raw)

    monkeypatch.setattr(efs, "parse_email_message", spy)
    server = FakeImapServer()
    server.add_message(_build_mime_email(attachments=[("r.pdf", PDF)], message_id="<t@t>").as_bytes())
    stats, _ = await _run(account_id, test_session_factory, mail_settings, server)
    assert stats["relevant"] == 1
    assert threads and all(name != threading.main_thread().name for name in threads), threads


async def test_mail_with_many_attachments_creates_capped_jobs(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    atts = [(f"r{i}.pdf", PDF) for i in range(efs._MAX_ATTACHMENTS_PER_EMAIL + 5)]
    server.add_message(_build_mime_email(attachments=atts, message_id="<many@t>").as_bytes())
    stats, _ = await _run(account_id, test_session_factory, mail_settings, server)
    assert stats["relevant"] == 1
    async with test_session_factory() as s:
        jobs = (await s.execute(select(ProcessingJob).where(ProcessingJob.source == JobSource.EMAIL))).scalars().all()
    assert len(jobs) == efs._MAX_ATTACHMENTS_PER_EMAIL
    records = await _records(test_session_factory, account_id)
    assert len(records) == 1
