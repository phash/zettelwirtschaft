import asyncio
import socket
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import Settings
from app.models.email_account import EmailAccount, ScheduleType
from app.models.notification import Notification, NotificationType
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.models.processing_job import JobSource, ProcessingJob
from app.services import email_fetch_service
from app.services.crypto_service import encrypt_password
from app.services.email_fetch_service import (
    _connect_imap,
    fetch_emails_for_account,
    parse_email_message,
    test_imap_connection as imap_connection_test,
)
from tests.credentials import TEST_EMAIL_ENCRYPTION_KEY, TEST_IMAP_PASSWORD
from tests.services.fake_imap import FakeImapServer

TARGET = "Zettelwirtschaft/Verarbeitet"
PDF = b"%PDF-1.4 fake content"
RELEVANT = {"relevant": True, "reason": "Rechnung"}
IRRELEVANT = {"relevant": False, "reason": "Newsletter"}


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
    if message_id is not None:
        msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 03 Mar 2026 10:00:00 +0100"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for name, content in (attachments or []):
        att = MIMEApplication(content, Name=name)
        att["Content-Disposition"] = f'attachment; filename="{name}"'
        msg.attach(att)
    return msg


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_email_message():
    msg = _build_mime_email(
        subject="Ihre Rechnung Nr. 123",
        sender="firma@test.de",
        body="Sehr geehrter Kunde, anbei Ihre Rechnung.",
        attachments=[("rechnung.pdf", PDF)],
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


def _part_with_headers(content_type: str, payload: bytes, headers: dict) -> MIMEApplication:
    maintype, subtype = content_type.split("/")
    part = MIMEApplication(payload, _subtype=subtype)
    if maintype != "application":
        part.replace_header("Content-Type", content_type)
    for key, value in headers.items():
        part[key] = value
    return part


@pytest.mark.parametrize("headers", [
    {"Content-Disposition": 'inline; filename="rechnung.pdf"'},          # Apple Mail
    {"Content-Disposition": 'Attachment; filename="rechnung.pdf"'},      # Grossschreibung
], ids=["inline", "capitalised"])
def test_parse_recognises_inline_and_capitalised_attachments(headers):
    msg = _build_mime_email(attachments=[])
    msg.attach(_part_with_headers("application/pdf", PDF, headers))
    parsed = parse_email_message(msg.as_bytes())
    assert [a["filename"] for a in parsed["attachments"]] == ["rechnung.pdf"]
    assert parsed["attachments"][0]["content"] == PDF


def test_parse_recognises_name_only_attachment():
    msg = _build_mime_email(attachments=[])
    part = MIMEApplication(PDF, _subtype="pdf")
    part.set_param("name", "rechnung.pdf")  # nur Content-Type name=, keine Disposition
    msg.attach(part)
    parsed = parse_email_message(msg.as_bytes())
    assert [a["filename"] for a in parsed["attachments"]] == ["rechnung.pdf"]


def test_parse_ignores_inline_cid_logo():
    msg = _build_mime_email(attachments=[])
    msg.attach(_part_with_headers("image/png", b"\x89PNG\r\n\x1a\nlogo", {
        "Content-Disposition": 'inline; filename="logo.png"',
        "Content-ID": "<logo@firma>",
    }))
    assert parse_email_message(msg.as_bytes())["attachments"] == []


def test_parse_html_only_body():
    msg = EmailMessage()
    msg["Subject"] = "Rechnung"
    msg["Message-ID"] = "<html@test>"
    msg.set_content(
        "<html><head><style>p{color:red}</style></head><body>"
        "<p>Rechnungsbetrag:&nbsp;12,50&nbsp;&euro;</p><script>alert(1)</script>"
        "<div>Fällig am 01.04.</div></body></html>",
        subtype="html",
    )
    body = parse_email_message(msg.as_bytes())["body"]
    assert "Rechnungsbetrag: 12,50 €" in body
    assert "Fällig am 01.04." in body
    assert "color" not in body and "alert" not in body


def test_parse_multipart_alternative_prefers_plain():
    msg = EmailMessage()
    msg["Message-ID"] = "<alt@test>"
    msg.set_content("Klartext-Version")
    msg.add_alternative("<p>HTML-Version</p>", subtype="html")
    body = parse_email_message(msg.as_bytes())["body"]
    assert "Klartext-Version" in body
    assert "HTML-Version" not in body


def test_parse_html_entities_are_decoded_once():
    msg = EmailMessage()
    msg["Message-ID"] = "<ent@test>"
    msg.set_content("<p>Code: &amp;lt;b&amp;gt; &amp; AT&amp;T</p>", subtype="html")
    assert parse_email_message(msg.as_bytes())["body"] == "Code: &lt;b&gt; & AT&T"


def test_parse_unknown_charsets_do_not_fail():
    raw = (
        b"Message-ID: <cs@test>\r\n"
        b"Subject: =?unknown-8bit?Q?Rechnung_M=E4rz?=\r\n"
        b"From: firma@test.de\r\n"
        b"Content-Type: text/plain; charset=x-unknown\r\n"
        b"\r\n"
        b"Betrag 10 EUR\r\n"
    )
    parsed = parse_email_message(raw)
    assert parsed["subject"].startswith("Rechnung M")
    assert "Betrag 10 EUR" in parsed["body"]


def test_parse_encoded_header_has_no_double_spaces():
    raw = (
        b"Message-ID: <sp@test>\r\n"
        b"Subject: =?utf-8?Q?Ihre_Rechnung?= =?utf-8?Q?_f=C3=BCr_M=C3=A4rz?=\r\n"
        b"\r\nText\r\n"
    )
    assert parse_email_message(raw)["subject"] == "Ihre Rechnung für März"


def test_parse_without_message_id_uses_stable_surrogate():
    a = _build_mime_email(subject="A", message_id=None).as_bytes()
    b = _build_mime_email(subject="B", message_id=None).as_bytes()
    id_a, id_b = parse_email_message(a)["message_id"], parse_email_message(b)["message_id"]
    assert id_a.startswith("noid-") and id_b.startswith("noid-")
    assert id_a != id_b
    assert parse_email_message(a)["message_id"] == id_a


# ---------------------------------------------------------------------------
# Abruf mit Fake-IMAP-Server
# ---------------------------------------------------------------------------


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


def _patch_imap(server: FakeImapServer):
    """Ersetzt nur den Socket-Teil — Login, Fehler-Mapping etc. laufen echt."""
    return patch.object(email_fetch_service.imaplib, "IMAP4_SSL", side_effect=server.connection)


def _patch_relevance(**kwargs):
    return patch(
        "app.services.email_fetch_service.check_email_relevance", new_callable=AsyncMock, **kwargs
    )


async def _run(account_id, factory, settings, server, relevance=RELEVANT, **relevance_kwargs):
    kwargs = relevance_kwargs or {"return_value": relevance}
    with _patch_imap(server), _patch_relevance(**kwargs) as rel:
        stats = await fetch_emails_for_account(account_id, factory, settings)
    return stats, rel


async def _records(factory, account_id):
    async with factory() as s:
        rows = await s.execute(
            select(ProcessedEmail).where(ProcessedEmail.email_account_id == account_id)
            .order_by(ProcessedEmail.id)
        )
        return rows.scalars().all()


async def _account(factory, account_id) -> EmailAccount:
    async with factory() as s:
        return await s.get(EmailAccount, account_id)


async def test_relevant_mail_creates_job_and_is_moved(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    server.add_message(_build_mime_email(attachments=[("rechnung.pdf", PDF)], message_id="<new@t>").as_bytes())
    user_deleted = server.add_message(b"Message-ID: <old@t>\r\n\r\nalt", flags={"\\Seen", "\\Deleted"})

    stats, _ = await _run(account_id, test_session_factory, mail_settings, server)

    assert stats["relevant"] == 1 and stats["error"] is None
    async with test_session_factory() as s:
        jobs = (await s.execute(select(ProcessingJob).where(ProcessingJob.source == JobSource.EMAIL))).scalars().all()
    assert len(jobs) == 1 and Path(jobs[0].file_path).read_bytes() == PDF
    assert [r.status for r in await _records(test_session_factory, account_id)] == [EmailStatus.RELEVANT]
    # Zielordner wurde angelegt, Mail per UID MOVE verschoben
    assert "CREATE" in server.command_names()
    assert len(server.folders[TARGET]) == 1
    # Abholen nur mit PEEK auf read-only Ordner
    # (RFC822.SIZE liest nur Metadaten und setzt kein \Seen)
    assert all("PEEK" in c[3] for c in server.uid_commands("FETCH") if c[3] != "(RFC822.SIZE)")
    assert ("EXAMINE", "INBOX") in server.commands
    # Nie pauschales EXPUNGE/CLOSE: vom Benutzer geloeschte Mail bleibt erhalten
    assert "EXPUNGE" not in server.command_names() and "CLOSE" not in server.command_names()
    assert user_deleted in server.folders["INBOX"]
    assert (await _account(test_session_factory, account_id)).last_error is None


async def test_copy_rejected_never_sets_deleted_or_expunges(account_id, test_session_factory, mail_settings):
    """Regression (Blocker): COPY 'NO [TRYCREATE]' darf nicht zum Loeschen fuehren."""
    server = FakeImapServer(capabilities=("IMAP4REV1", "UIDPLUS"), copy_ok=False)
    uid = server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())

    stats, _ = await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)

    assert stats["irrelevant"] == 1
    assert server.uid_commands("COPY")
    assert not server.uid_commands("EXPUNGE") and "EXPUNGE" not in server.command_names()
    assert all("\\Deleted" not in c[4] for c in server.uid_commands("STORE"))
    assert server.folders["INBOX"][uid]["flags"] == {"\\Seen"}
    assert "nur als gelesen markiert" in (await _account(test_session_factory, account_id)).last_error


async def test_copy_without_uidplus_does_not_expunge(account_id, test_session_factory, mail_settings):
    server = FakeImapServer(capabilities=("IMAP4REV1",))
    uid = server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())

    await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)

    assert len(server.folders[TARGET]) == 1
    assert server.folders["INBOX"][uid]["flags"] == {"\\Seen", "\\Deleted"}
    assert "EXPUNGE" not in server.command_names() and not server.uid_commands("EXPUNGE")


async def test_copy_with_uidplus_expunges_only_processed_uids(account_id, test_session_factory, mail_settings):
    server = FakeImapServer(capabilities=("IMAP4REV1", "UIDPLUS"))
    server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())
    foreign = server.add_message(b"Message-ID: <x@t>\r\n\r\nx", flags={"\\Seen", "\\Deleted"})

    await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)

    assert [c[2] for c in server.uid_commands("EXPUNGE")] == ["1"]
    assert foreign in server.folders["INBOX"]
    assert len(server.folders[TARGET]) == 1


async def test_missing_target_folder_that_cannot_be_created(account_id, test_session_factory, mail_settings):
    server = FakeImapServer(create_ok=False)
    uid = server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())

    await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)

    assert not server.uid_commands("MOVE") and not server.uid_commands("COPY")
    assert server.folders["INBOX"][uid]["flags"] == {"\\Seen"}
    assert "konnte nicht angelegt werden" in (await _account(test_session_factory, account_id)).last_error


async def test_target_folder_uses_server_delimiter(account_id, test_session_factory, mail_settings):
    server = FakeImapServer(delimiter=".")
    server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())

    await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)

    assert len(server.folders["Zettelwirtschaft.Verarbeitet"]) == 1


async def test_target_folder_uses_backslash_delimiter(account_id, test_session_factory, mail_settings):
    """LIST liefert den Trenner als quoted String '"\\\\"' — muss zu '\\' werden."""
    server = FakeImapServer(delimiter="\\")
    server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())

    await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)

    assert len(server.folders["Zettelwirtschaft\\Verarbeitet"]) == 1


async def test_uidvalidity_change_skips_filing(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    uid = server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())

    def change_validity(srv, connection_no):
        if connection_no == 2:
            srv.uidvalidity = "999"

    server.on_select = change_validity
    await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)

    assert not server.uid_commands("MOVE") and not server.uid_commands("STORE")
    assert server.folders["INBOX"][uid]["flags"] == set()
    # Naechster Lauf: Duplikat erkannt (kein LLM-Call) und abgelegt
    server.on_select = None
    stats, rel = await _run(account_id, test_session_factory, mail_settings, server)
    assert stats["skipped"] == 1 and rel.await_count == 0
    assert len(server.folders[TARGET]) == 1


async def test_already_processed_mail_is_skipped_and_filed(account_id, test_session_factory, mail_settings):
    async with test_session_factory() as s:
        s.add(ProcessedEmail(email_account_id=account_id, message_id="<already@test.com>",
                             status=EmailStatus.IRRELEVANT))
        await s.commit()
    server = FakeImapServer()
    server.add_message(_build_mime_email(message_id="<already@test.com>").as_bytes())

    stats, rel = await _run(account_id, test_session_factory, mail_settings, server)

    assert stats["skipped"] == 1 and stats["relevant"] == 0
    rel.assert_not_awaited()
    assert len(server.folders[TARGET]) == 1


async def test_no_unseen_mails(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    server.add_message(b"Message-ID: <r@t>\r\n\r\ngelesen", flags={"\\Seen"})
    stats, _ = await _run(account_id, test_session_factory, mail_settings, server)
    assert stats["total"] == 0 and stats["error"] is None
    assert (await _account(test_session_factory, account_id)).last_checked_at is not None


async def test_failure_of_first_mail_does_not_break_the_next(account_id, test_session_factory, mail_settings):
    """Regression: frueher MissingGreenlet nach Rollback fuer alle folgenden Mails."""
    server = FakeImapServer()
    server.add_message(_build_mime_email(message_id="<crash@t>").as_bytes())
    server.add_message(_build_mime_email(attachments=[("r.pdf", PDF)], message_id="<ok@t>").as_bytes())

    stats, _ = await _run(
        account_id, test_session_factory, mail_settings, server,
        side_effect=[RuntimeError("LLM down"), RELEVANT],
    )

    assert stats["failed"] == 1 and stats["relevant"] == 1 and stats["total"] == 2
    records = await _records(test_session_factory, account_id)
    assert [(r.message_id, r.status) for r in records] == [
        ("<crash@t>", EmailStatus.FAILED), ("<ok@t>", EmailStatus.RELEVANT),
    ]
    # SEC-DATA-05: Exception-Text (Pfade/SQL) nicht in der History, nur der Typ.
    assert "LLM down" not in records[0].relevance_reason
    assert "RuntimeError" in records[0].relevance_reason
    # R-06: beide abgelegt, keine Endlos-UNSEEN
    assert len(server.folders[TARGET]) == 2
    assert (await _account(test_session_factory, account_id)).last_error == "1 von 2 E-Mails fehlgeschlagen"


async def test_failed_mail_leaves_no_orphan_upload_files(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    server.add_message(_build_mime_email(attachments=[("r.pdf", PDF)], message_id="<o@t>").as_bytes())
    original = email_fetch_service._create_jobs_from_email

    async def create_then_crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("DB kaputt")

    with patch.object(email_fetch_service, "_create_jobs_from_email", create_then_crash):
        stats, _ = await _run(account_id, test_session_factory, mail_settings, server)

    assert stats["failed"] == 1
    assert list(Path(mail_settings.UPLOAD_DIR).iterdir()) == []
    async with test_session_factory() as s:
        assert (await s.execute(select(ProcessingJob))).scalars().all() == []


async def test_cancelled_mail_leaves_no_orphan_upload_files(account_id, test_session_factory, mail_settings):
    """Abbruch (z.B. Herunterfahren) mitten in der Mail: keine verwaisten Uploads."""
    server = FakeImapServer()
    server.add_message(_build_mime_email(attachments=[("r.pdf", PDF)], message_id="<c@t>").as_bytes())
    original = email_fetch_service._create_jobs_from_email

    async def create_then_cancel(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    with patch.object(email_fetch_service, "_create_jobs_from_email", create_then_cancel):
        with pytest.raises(asyncio.CancelledError):
            await _run(account_id, test_session_factory, mail_settings, server)

    assert list(Path(mail_settings.UPLOAD_DIR).iterdir()) == []


async def test_mails_without_message_id_are_all_processed(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    raw_a = _build_mime_email(subject="A", message_id=None).as_bytes()
    server.add_message(raw_a)
    server.add_message(_build_mime_email(subject="B", message_id=None).as_bytes())

    stats, rel = await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)
    assert stats["irrelevant"] == 2 and rel.await_count == 2

    # Dieselbe Mail erneut (z.B. wieder als ungelesen markiert) -> Duplikat
    server.add_message(raw_a)
    stats, rel = await _run(account_id, test_session_factory, mail_settings, server)
    assert stats["skipped"] == 1 and rel.await_count == 0


async def test_relevant_mail_without_usable_document_is_failed(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    server.add_message(_build_mime_email(
        body="Kurz.", attachments=[("vertrag.docx", b"PK\x03\x04docx")], message_id="<d@t>",
    ).as_bytes())

    stats, _ = await _run(account_id, test_session_factory, mail_settings, server)

    assert stats["failed"] == 1 and stats["relevant"] == 0
    [record] = await _records(test_session_factory, account_id)
    assert record.status == EmailStatus.FAILED and record.processing_job_id is None
    assert "vertrag.docx" in record.relevance_reason
    async with test_session_factory() as s:
        notes = (await s.execute(select(Notification))).scalars().all()
    assert [n.type for n in notes] == [NotificationType.SYSTEM]
    assert len(server.folders[TARGET]) == 1


async def test_long_body_without_attachment_becomes_text_job(account_id, test_session_factory, mail_settings):
    server = FakeImapServer()
    body = "Rechnung Nr. 4711 über 99,00 EUR für Wartungsarbeiten. " * 4
    server.add_message(_build_mime_email(body=body, subject='Re: "Rechnung"?', message_id="<b@t>").as_bytes())

    stats, _ = await _run(account_id, test_session_factory, mail_settings, server)

    assert stats["relevant"] == 1
    async with test_session_factory() as s:
        [job] = (await s.execute(select(ProcessingJob))).scalars().all()
    assert job.file_type == "txt" and job.ocr_text.startswith("Rechnung Nr. 4711")
    assert not any(c in job.stored_filename for c in '<>:"/\\|?*')


async def test_tif_attachment_gets_canonical_file_type(account_id, test_session_factory, mail_settings):
    """'.TIF'-Anhaenge laufen wie Uploads durch die gemeinsame Job-Erzeugung:
    kanonischer Typ 'tiff', den Thumbnail/OCR kennen."""
    server = FakeImapServer()
    tiff = b"II*\x00" + b"\x00" * 64
    server.add_message(_build_mime_email(attachments=[("SCAN.TIF", tiff)], message_id="<tif@t>").as_bytes())

    stats, _ = await _run(account_id, test_session_factory, mail_settings, server)

    assert stats["relevant"] == 1, stats
    async with test_session_factory() as s:
        [job] = (await s.execute(select(ProcessingJob).where(ProcessingJob.source == JobSource.EMAIL))).scalars().all()
    assert job.file_type == "tiff"
    assert job.email_account_id == account_id


async def test_oversized_attachment_is_rejected(account_id, test_session_factory, mail_settings):
    settings = mail_settings.model_copy(update={"MAX_UPLOAD_SIZE_MB": 1})
    server = FakeImapServer()
    big = b"%PDF-1.4" + b"0" * (1024 * 1024 + 10)
    server.add_message(_build_mime_email(body="x", attachments=[("gross.pdf", big)], message_id="<g@t>").as_bytes())

    with patch.object(email_fetch_service, "generate_stored_filename",
                      wraps=email_fetch_service.generate_stored_filename) as stored_name:
        stats, _ = await _run(account_id, test_session_factory, settings, server)

    assert stats["failed"] == 1
    assert list(Path(settings.UPLOAD_DIR).iterdir()) == []
    # Wie beim HTTP-Upload: Groesse VOR dem Schreiben geprueft -> nie eine
    # Upload-Datei angelegt (kein Dateiname erzeugt).
    stored_name.assert_not_called()
    [record] = await _records(test_session_factory, account_id)
    assert "gross.pdf" in record.relevance_reason and "zu groß" in record.relevance_reason


async def test_allowed_type_without_magic_signature_is_rejected(account_id, test_session_factory, mail_settings):
    """Frueher fail-open: Typen ohne MAGIC_BYTES-Eintrag kamen ungeprueft durch."""
    settings = mail_settings.model_copy(update={"ALLOWED_FILE_TYPES": "pdf,gif"})
    server = FakeImapServer()
    server.add_message(_build_mime_email(body="x", attachments=[("bild.gif", b"GIF89a...")], message_id="<m@t>").as_bytes())

    stats, _ = await _run(account_id, test_session_factory, settings, server)

    assert stats["failed"] == 1
    assert list(Path(settings.UPLOAD_DIR).iterdir()) == []


async def test_max_per_run_processes_oldest_first(account_id, test_session_factory, mail_settings):
    settings = mail_settings.model_copy(update={"EMAIL_MAX_PER_RUN": 2})
    server = FakeImapServer()
    for i in range(3):
        server.add_message(_build_mime_email(message_id=f"<m{i}@t>").as_bytes())

    stats, _ = await _run(account_id, test_session_factory, settings, server, relevance=IRRELEVANT)

    assert stats["total"] == 2 and stats["remaining"] == 1
    assert [r.message_id for r in await _records(test_session_factory, account_id)] == ["<m0@t>", "<m1@t>"]
    assert server.body_fetches == [1, 2]


async def test_oversized_mail_is_not_downloaded(account_id, test_session_factory, mail_settings):
    """FH-05: Mails ueber der Grenze werden nicht geladen, sondern sichtbar als
    FAILED verbucht, gemeldet und abgelegt (blockieren keinen spaeteren Lauf)."""
    settings = mail_settings.model_copy(update={"MAX_UPLOAD_SIZE_MB": 1})
    server = FakeImapServer()
    big = server.add_message(_build_mime_email(
        subject="Riesig", attachments=[("rechnung.pdf", PDF)], message_id="<big@t>",
    ).as_bytes())
    server.reported_sizes[big] = 3 * 1024 * 1024  # > 2 x MAX_UPLOAD_SIZE_MB
    small = server.add_message(_build_mime_email(attachments=[("klein.pdf", PDF)], message_id="<small@t>").as_bytes())

    stats, rel = await _run(account_id, test_session_factory, settings, server)

    assert server.body_fetches == [small]  # die grosse Mail wurde nie komplett geladen
    assert stats["total"] == 2 and stats["failed"] == 1 and stats["relevant"] == 1
    assert rel.await_count == 1  # kein LLM-Aufruf fuer die grosse Mail
    records = {r.message_id: r for r in await _records(test_session_factory, account_id)}
    assert records["<big@t>"].status == EmailStatus.FAILED
    assert records["<big@t>"].subject == "Riesig"
    assert "zu groß" in records["<big@t>"].relevance_reason
    assert len(server.folders[TARGET]) == 2  # beide abgelegt
    async with test_session_factory() as s:
        notes = (await s.execute(select(Notification).where(Notification.type == NotificationType.SYSTEM))).scalars().all()
    assert any("zu groß" in n.title for n in notes)

    # Zweiter Lauf: nichts mehr zu tun, kein Doppel-Eintrag.
    stats, _ = await _run(account_id, test_session_factory, settings, server)
    assert stats["total"] == 0
    assert len(await _records(test_session_factory, account_id)) == 2


async def test_wrong_reported_size_is_checked_after_download(account_id, test_session_factory, mail_settings):
    settings = mail_settings.model_copy(update={"MAX_UPLOAD_SIZE_MB": 1})
    server = FakeImapServer()
    raw = _build_mime_email(attachments=[("x.pdf", b"%PDF-1.4" + b"0" * (2 * 1024 * 1024 + 10))], message_id="<luege@t>").as_bytes()
    uid = server.add_message(raw)
    server.reported_sizes[uid] = 100  # Server meldet falsche Groesse

    stats, rel = await _run(account_id, test_session_factory, settings, server)

    assert stats["failed"] == 1 and rel.await_count == 0
    [record] = await _records(test_session_factory, account_id)
    assert record.message_id == "<luege@t>" and "zu groß" in record.relevance_reason


async def test_batch_byte_budget_defers_rest(account_id, test_session_factory, mail_settings):
    """FH-05: Pro Lauf liegen hoechstens ~2 grosse Mails im Speicher, der Rest
    folgt beim naechsten Abruf."""
    settings = mail_settings.model_copy(update={"MAX_UPLOAD_SIZE_MB": 1})
    server = FakeImapServer()
    for i in range(4):
        uid = server.add_message(_build_mime_email(message_id=f"<b{i}@t>").as_bytes())
        server.reported_sizes[uid] = 1536 * 1024  # je 1,5 MB, Budget 4 MB

    stats, _ = await _run(account_id, test_session_factory, settings, server, relevance=IRRELEVANT)

    assert server.body_fetches == [1, 2]
    assert stats["total"] == 2 and stats["remaining"] == 2
    stats, _ = await _run(account_id, test_session_factory, settings, server, relevance=IRRELEVANT)
    assert server.body_fetches == [1, 2, 3, 4] and stats["remaining"] == 0


async def test_wrong_encryption_key_gives_specific_error(account_id, test_session_factory, mail_settings):
    settings = mail_settings.model_copy(update={"EMAIL_ENCRYPTION_KEY": Fernet.generate_key().decode()})
    server = FakeImapServer()

    stats, _ = await _run(account_id, test_session_factory, settings, server)

    assert "Passwort kann nicht entschlüsselt werden" in stats["error"]
    assert (await _account(test_session_factory, account_id)).last_error == stats["error"]
    assert server.connections == 0


async def test_connection_error_is_classified(account_id, test_session_factory, mail_settings):
    with patch.object(email_fetch_service.imaplib, "IMAP4_SSL",
                      side_effect=socket.gaierror(11001, "getaddrinfo failed for intern.lan")):
        stats = await fetch_emails_for_account(account_id, test_session_factory, mail_settings)
    assert stats["error"] == "Server nicht gefunden — Hostname prüfen."
    last_error = (await _account(test_session_factory, account_id)).last_error
    assert last_error == stats["error"] and "intern.lan" not in last_error


async def test_login_failure_is_reported(account_id, test_session_factory, mail_settings):
    stats, _ = await _run(account_id, test_session_factory, mail_settings, FakeImapServer(login_ok=False))
    assert stats["error"] == "Anmeldung fehlgeschlagen — Benutzername und Passwort prüfen."


async def test_last_error_is_cleared_after_clean_run(account_id, test_session_factory, mail_settings):
    async with test_session_factory() as s:
        (await s.get(EmailAccount, account_id)).last_error = "alt"
        await s.commit()
    server = FakeImapServer()
    server.add_message(_build_mime_email(message_id="<n@t>").as_bytes())
    await _run(account_id, test_session_factory, mail_settings, server, relevance=IRRELEVANT)
    assert (await _account(test_session_factory, account_id)).last_error is None


# ---------------------------------------------------------------------------
# Verbindungstest + Timeout
# ---------------------------------------------------------------------------


async def test_connection_test_warns_about_missing_target_folder(account_id, test_session_factory, mail_settings):
    account = await _account(test_session_factory, account_id)
    with _patch_imap(FakeImapServer()):
        result = await imap_connection_test(account, mail_settings)
    assert result["success"] is True
    assert "wird beim ersten Abruf angelegt" in result["warning"]


async def test_connection_test_reports_missing_inbox(account_id, test_session_factory, mail_settings):
    account = await _account(test_session_factory, account_id)
    with _patch_imap(FakeImapServer(folders=("Posteingang",))):
        result = await imap_connection_test(account, mail_settings)
    assert result == {"success": False, "error": "Postfach-Ordner „INBOX“ nicht gefunden.", "warning": None}


async def test_connection_test_times_out_on_silent_server(account_id, test_session_factory, mail_settings):
    """Stummer Server (z.B. SSL aus auf Port 993) darf nicht ewig blockieren."""
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        port = silent.getsockname()[1]
        async with test_session_factory() as s:
            account = await s.get(EmailAccount, account_id)
            account.imap_host, account.imap_port, account.use_ssl = "127.0.0.1", port, False
            await s.commit()
        settings = mail_settings.model_copy(update={"IMAP_TIMEOUT": 1})
        result = await imap_connection_test(account, settings)
    assert result["success"] is False
    assert result["error"].startswith("Zeitüberschreitung — Server antwortet nicht (Port/SSL prüfen).")


def test_connect_imap_passes_timeout():
    class Acc:
        use_ssl, imap_host, imap_port, username = True, "h", 993, "u"

    server = FakeImapServer()
    with patch.object(email_fetch_service.imaplib, "IMAP4_SSL", side_effect=server.connection) as ctor:
        _connect_imap(Acc(), "pw", 17)
    assert ctor.call_args.kwargs["timeout"] == 17
