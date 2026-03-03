# E-Mail-Anbindung Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatischer IMAP-Abruf von E-Mails aus mehreren Konten mit LLM-Relevanzpruefung, einspeisen relevanter Dokumente in die bestehende Verarbeitungs-Pipeline.

**Architecture:** Neuer IMAP-Polling-Service als Background-Task im Backend. E-Mail-Konten werden ueber die Web-UI konfiguriert und in der DB gespeichert (Passwoerter Fernet-verschluesselt). Pro Konto werden ungelesene E-Mails abgerufen, per LLM auf Relevanz geprueft, und relevante Anhaenge/Texte als ProcessingJobs in die bestehende Queue eingespeist. Scheduling via CRON/MANUAL/IDLE.

**Tech Stack:** Python imaplib + email (Standardbibliothek), cryptography (Fernet), croniter (CRON-Parsing), SQLAlchemy async, Vue.js 3

**Design-Dokument:** `docs/plans/2026-03-03-email-integration-design.md`

---

## Task 1: Dependencies hinzufuegen

**Files:**
- Modify: `backend/requirements.txt`

**Step 1: Dependencies ergaenzen**

In `backend/requirements.txt` vor dem `# Testing` Kommentar einfuegen:

```
cryptography==44.*
croniter==6.*
```

**Step 2: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: cryptography + croniter Dependencies fuer E-Mail-Anbindung"
```

---

## Task 2: Datenmodell — EmailAccount + ProcessedEmail + Migration

**Files:**
- Create: `backend/app/models/email_account.py`
- Create: `backend/app/models/processed_email.py`
- Modify: `backend/app/models/processing_job.py` (JobSource Enum + email_account_id FK)
- Create: `backend/alembic/versions/009_add_email_accounts.py`
- Modify: `backend/tests/conftest.py` (Model-Import)

### Step 1: EmailAccount-Modell erstellen

Erstelle `backend/app/models/email_account.py`:

```python
import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduleType(str, enum.Enum):
    CRON = "CRON"
    MANUAL = "MANUAL"
    IDLE = "IDLE"


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    imap_host: Mapped[str] = mapped_column(String(500), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[str] = mapped_column(String(500), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    folder_inbox: Mapped[str] = mapped_column(String(500), nullable=False, default="INBOX")
    folder_processed: Mapped[str] = mapped_column(
        String(500), nullable=False, default="Zettelwirtschaft/Verarbeitet"
    )
    schedule_type: Mapped[str] = mapped_column(
        Enum(ScheduleType, native_enum=False), nullable=False, default=ScheduleType.MANUAL
    )
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    filing_scope_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("filing_scopes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

### Step 2: ProcessedEmail-Modell erstellen

Erstelle `backend/app/models/processed_email.py`:

```python
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailStatus(str, enum.Enum):
    RELEVANT = "RELEVANT"
    IRRELEVANT = "IRRELEVANT"
    FAILED = "FAILED"


class ProcessedEmail(Base):
    __tablename__ = "processed_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(1000), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(EmailStatus, native_enum=False), nullable=False
    )
    relevance_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processing_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

**Wichtig:** `message_id` ist UNIQUE pro Account, nicht global. Constraint via UniqueConstraint:

```python
from sqlalchemy import UniqueConstraint

class ProcessedEmail(Base):
    __tablename__ = "processed_emails"
    __table_args__ = (
        UniqueConstraint("email_account_id", "message_id", name="uq_account_message"),
    )
    # ... rest wie oben
```

### Step 3: JobSource Enum erweitern + email_account_id auf ProcessingJob

In `backend/app/models/processing_job.py`:

- `JobSource` Enum: Wert `EMAIL = "EMAIL"` hinzufuegen (Zeile 12)
- `ProcessingJob`: Feld `email_account_id` hinzufuegen (nullable FK zu email_accounts)

```python
class JobSource(str, enum.Enum):
    UPLOAD = "UPLOAD"
    WATCH_FOLDER = "WATCH_FOLDER"
    EMAIL = "EMAIL"
```

Neues Feld auf ProcessingJob (nach `retry_count`):

```python
    email_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
```

### Step 4: Alembic-Migration erstellen

Erstelle `backend/alembic/versions/009_add_email_accounts.py`:

```python
"""E-Mail-Konten und verarbeitete E-Mails.

Revision ID: 009_add_email_accounts
Revises: 008_add_warranty_reminder_flags
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa

revision = "009_add_email_accounts"
down_revision = "008_add_warranty_reminder_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("imap_host", sa.String(500), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("username", sa.String(500), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("folder_inbox", sa.String(500), nullable=False, server_default="'INBOX'"),
        sa.Column(
            "folder_processed",
            sa.String(500),
            nullable=False,
            server_default="'Zettelwirtschaft/Verarbeitet'",
        ),
        sa.Column("schedule_type", sa.String(10), nullable=False, server_default="'MANUAL'"),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "filing_scope_id",
            sa.Integer(),
            sa.ForeignKey("filing_scopes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "processed_emails",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "email_account_id",
            sa.Integer(),
            sa.ForeignKey("email_accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("message_id", sa.String(1000), nullable=False),
        sa.Column("subject", sa.String(1000), nullable=True),
        sa.Column("sender", sa.String(500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("relevance_reason", sa.String(500), nullable=True),
        sa.Column(
            "processing_job_id",
            sa.String(36),
            sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("email_account_id", "message_id", name="uq_account_message"),
    )

    # email_account_id auf processing_jobs
    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.add_column(sa.Column("email_account_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch_op:
        batch_op.drop_column("email_account_id")
    op.drop_table("processed_emails")
    op.drop_table("email_accounts")
```

### Step 5: conftest.py Model-Imports ergaenzen

In `backend/tests/conftest.py` nach den bestehenden Model-Imports (Zeile ~24) hinzufuegen:

```python
import app.models.email_account  # noqa: F401
import app.models.processed_email  # noqa: F401
```

### Step 6: Tests schreiben und ausfuehren

Erstelle `backend/tests/models/test_email_models.py`:

```python
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
```

Run: `cd backend && python -m pytest tests/models/test_email_models.py -v`

### Step 7: Commit

```bash
git add backend/app/models/email_account.py backend/app/models/processed_email.py \
  backend/app/models/processing_job.py backend/alembic/versions/009_add_email_accounts.py \
  backend/tests/conftest.py backend/tests/models/test_email_models.py
git commit -m "feat: EmailAccount + ProcessedEmail Modelle + Migration 009 (Issue #18)"
```

---

## Task 3: Passwort-Verschluesselung (Crypto-Service)

**Files:**
- Create: `backend/app/services/crypto_service.py`
- Modify: `backend/app/config.py` (EMAIL_ENCRYPTION_KEY Setting)
- Create: `backend/tests/services/test_crypto_service.py`

### Step 1: Config erweitern

In `backend/app/config.py`, Settings-Klasse — neues Feld nach den ChromaDB-Settings:

```python
    # E-Mail
    EMAIL_ENCRYPTION_KEY: str = ""
```

### Step 2: Test schreiben

Erstelle `backend/tests/services/test_crypto_service.py`:

```python
import pytest

from app.services.crypto_service import encrypt_password, decrypt_password, generate_encryption_key


def test_encrypt_decrypt_roundtrip():
    key = generate_encryption_key()
    password = "mein-geheimes-passwort"
    encrypted = encrypt_password(password, key)
    assert encrypted != password
    decrypted = decrypt_password(encrypted, key)
    assert decrypted == password


def test_encrypt_produces_different_ciphertexts():
    key = generate_encryption_key()
    enc1 = encrypt_password("test", key)
    enc2 = encrypt_password("test", key)
    # Fernet nutzt Zeitstempel + IV, daher unterschiedlich
    assert enc1 != enc2


def test_decrypt_with_wrong_key_fails():
    key1 = generate_encryption_key()
    key2 = generate_encryption_key()
    encrypted = encrypt_password("test", key1)
    with pytest.raises(Exception):
        decrypt_password(encrypted, key2)


def test_empty_password():
    key = generate_encryption_key()
    encrypted = encrypt_password("", key)
    assert decrypt_password(encrypted, key) == ""
```

Run: `cd backend && python -m pytest tests/services/test_crypto_service.py -v`
Expected: FAIL (module not found)

### Step 3: Implementieren

Erstelle `backend/app/services/crypto_service.py`:

```python
"""Passwort-Verschluesselung fuer E-Mail-Konten via Fernet (AES-128-CBC)."""

from cryptography.fernet import Fernet


def generate_encryption_key() -> str:
    """Generiert einen neuen Fernet-Schluessel."""
    return Fernet.generate_key().decode()


def encrypt_password(password: str, key: str) -> str:
    """Verschluesselt ein Passwort mit dem gegebenen Fernet-Schluessel."""
    f = Fernet(key.encode())
    return f.encrypt(password.encode()).decode()


def decrypt_password(encrypted: str, key: str) -> str:
    """Entschluesselt ein Passwort mit dem gegebenen Fernet-Schluessel."""
    f = Fernet(key.encode())
    return f.decrypt(encrypted.encode()).decode()
```

### Step 4: Tests ausfuehren

Run: `cd backend && python -m pytest tests/services/test_crypto_service.py -v`
Expected: ALL PASS

### Step 5: Commit

```bash
git add backend/app/services/crypto_service.py backend/app/config.py \
  backend/tests/services/test_crypto_service.py
git commit -m "feat: Crypto-Service fuer E-Mail-Passwort-Verschluesselung"
```

---

## Task 4: E-Mail-Relevanzpruefung (LLM-Prompt)

**Files:**
- Create: `backend/app/prompts/email_relevance.txt`
- Create: `backend/app/services/email_relevance_service.py`
- Create: `backend/tests/services/test_email_relevance_service.py`

### Step 1: Prompt-Template erstellen

Erstelle `backend/app/prompts/email_relevance.txt`:

```
Du bist ein Dokumenten-Assistent. Entscheide ob diese E-Mail ein archivierungswuerdiges Dokument enthaelt.

Archivierungswuerdig sind:
- Rechnungen, Quittungen, Kaufbelege
- Vertraege (Miet-, Kauf-, Versicherungsvertraege)
- Amtliche Schreiben, Steuerbescheide
- Lohnabrechnungen, Kontoauszuege
- Garantiescheine, Gewaehrleistungsbelege
- Versicherungspolicen
- Arzt-/Handwerkerrechnungen
- Bedienungsanleitungen (als Anhang)

NICHT archivierungswuerdig:
- Newsletter, Werbung, Marketing-E-Mails
- Soziale Medien Benachrichtigungen
- Versandbestaetigungen ohne Rechnung
- Chat-Nachrichten, persoenliche Korrespondenz
- Spam, Phishing
- Automatische Systemmeldungen

E-Mail-Daten:
Absender: {sender}
Betreff: {subject}
Text (Auszug): {body_snippet}
Anhaenge: {attachment_names}

Antworte ausschliesslich im JSON-Format:
{{"relevant": true/false, "reason": "Kurze Begruendung auf Deutsch"}}
```

### Step 2: Test schreiben

Erstelle `backend/tests/services/test_email_relevance_service.py`:

```python
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.email_relevance_service import check_email_relevance


@pytest.mark.asyncio
async def test_relevant_email(test_settings):
    mock_response = json.dumps({"relevant": True, "reason": "Rechnung als PDF-Anhang"})
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await check_email_relevance(
            sender="firma@example.com",
            subject="Ihre Rechnung Nr. 2024-001",
            body_snippet="Anbei erhalten Sie Ihre Rechnung.",
            attachment_names=["Rechnung_2024-001.pdf"],
            settings=test_settings,
        )
    assert result["relevant"] is True
    assert "reason" in result


@pytest.mark.asyncio
async def test_irrelevant_email(test_settings):
    mock_response = json.dumps({"relevant": False, "reason": "Newsletter"})
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value=mock_response):
        result = await check_email_relevance(
            sender="news@shop.de",
            subject="Unsere Top-Angebote diese Woche",
            body_snippet="Entdecken Sie unsere neuesten Angebote...",
            attachment_names=[],
            settings=test_settings,
        )
    assert result["relevant"] is False


@pytest.mark.asyncio
async def test_llm_failure_returns_relevant(test_settings):
    """Bei LLM-Fehler: sicherheitshalber als relevant markieren."""
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value=None):
        result = await check_email_relevance(
            sender="test@test.com",
            subject="Test",
            body_snippet="Test",
            attachment_names=[],
            settings=test_settings,
        )
    assert result["relevant"] is True
    assert "fehler" in result["reason"].lower() or "fallback" in result["reason"].lower()


@pytest.mark.asyncio
async def test_malformed_json_returns_relevant(test_settings):
    with patch("app.services.email_relevance_service.call_llm", new_callable=AsyncMock, return_value="not json"):
        result = await check_email_relevance(
            sender="test@test.com",
            subject="Test",
            body_snippet="",
            attachment_names=[],
            settings=test_settings,
        )
    assert result["relevant"] is True
```

Run: `cd backend && python -m pytest tests/services/test_email_relevance_service.py -v`
Expected: FAIL

### Step 3: Implementieren

Erstelle `backend/app/services/email_relevance_service.py`:

```python
"""LLM-basierte Relevanzpruefung fuer E-Mails."""

import json
import logging

from app.config import Settings
from app.services.llm_service import call_llm, load_prompt_template

logger = logging.getLogger(__name__)


async def check_email_relevance(
    sender: str,
    subject: str,
    body_snippet: str,
    attachment_names: list[str],
    settings: Settings,
) -> dict:
    """Prueft per LLM ob eine E-Mail archivierungswuerdige Dokumente enthaelt.

    Returns:
        {"relevant": bool, "reason": str}
        Bei Fehler: {"relevant": True, "reason": "..."} (Fallback: lieber zu viel archivieren)
    """
    template = load_prompt_template("email_relevance.txt")
    prompt = template.format(
        sender=sender or "unbekannt",
        subject=subject or "(kein Betreff)",
        body_snippet=(body_snippet or "")[:1000],
        attachment_names=", ".join(attachment_names) if attachment_names else "keine",
    )

    try:
        response = await call_llm(prompt, settings)
        if not response:
            logger.warning("LLM-Antwort leer bei Relevanzpruefung")
            return {"relevant": True, "reason": "LLM-Fehler, Fallback: als relevant markiert"}

        data = json.loads(response)
        return {
            "relevant": bool(data.get("relevant", True)),
            "reason": str(data.get("reason", "")),
        }
    except json.JSONDecodeError:
        logger.warning("LLM-Antwort kein gueltiges JSON: %s", response[:200] if response else "")
        return {"relevant": True, "reason": "JSON-Fehler, Fallback: als relevant markiert"}
    except Exception:
        logger.exception("Fehler bei E-Mail-Relevanzpruefung")
        return {"relevant": True, "reason": "Unerwarteter Fehler, Fallback: als relevant markiert"}
```

### Step 4: Tests ausfuehren

Run: `cd backend && python -m pytest tests/services/test_email_relevance_service.py -v`
Expected: ALL PASS

### Step 5: Commit

```bash
git add backend/app/prompts/email_relevance.txt \
  backend/app/services/email_relevance_service.py \
  backend/tests/services/test_email_relevance_service.py
git commit -m "feat: LLM-Relevanzpruefung fuer E-Mails + Prompt-Template"
```

---

## Task 5: E-Mail-Fetch-Service (Kern-Logik)

**Files:**
- Create: `backend/app/services/email_fetch_service.py`
- Create: `backend/tests/services/test_email_fetch_service.py`

### Step 1: Tests schreiben

Erstelle `backend/tests/services/test_email_fetch_service.py`.

Wichtige Testfaelle:

```python
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
    """Bereits verarbeitete E-Mails (gleiche Message-ID) werden uebersprungen."""
    account = EmailAccount(
        name="Test",
        imap_host="imap.test.com",
        username="user@test.com",
        encrypted_password="enc_pw",
        schedule_type=ScheduleType.MANUAL,
    )
    db_session.add(account)
    await db_session.flush()

    # Simuliere bereits verarbeitete E-Mail
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
    """Relevante E-Mail mit Anhang erstellt ProcessingJob."""
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

    # ProcessingJob wurde erstellt
    result = await db_session.execute(select(ProcessingJob).where(ProcessingJob.source == JobSource.EMAIL))
    jobs = result.scalars().all()
    assert len(jobs) >= 1

    # ProcessedEmail wurde gespeichert
    result = await db_session.execute(select(ProcessedEmail))
    processed = result.scalars().all()
    assert len(processed) == 1
    assert processed[0].status == EmailStatus.RELEVANT
```

Run: `cd backend && python -m pytest tests/services/test_email_fetch_service.py -v`
Expected: FAIL

### Step 2: Implementieren

Erstelle `backend/app/services/email_fetch_service.py`:

```python
"""E-Mail-Abruf und -Verarbeitung via IMAP."""

import email
import email.utils
import imaplib
import logging
import shutil
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.email_account import EmailAccount
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.services.crypto_service import decrypt_password
from app.services.email_relevance_service import check_email_relevance

logger = logging.getLogger(__name__)


def _decode_header_value(value: str | None) -> str:
    """Dekodiert MIME-kodierte Header-Werte."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def parse_email_message(raw_bytes: bytes) -> dict:
    """Parst eine rohe E-Mail in strukturierte Daten.

    Returns:
        {
            "message_id": str,
            "subject": str,
            "sender": str,
            "date": datetime | None,
            "body": str,
            "attachments": [{"filename": str, "content": bytes, "content_type": str}]
        }
    """
    msg = email.message_from_bytes(raw_bytes)

    # Header
    message_id = msg.get("Message-ID", "")
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    date_tuple = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None

    # Body + Anhaenge
    body_parts = []
    attachments = []

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))

        if "attachment" in disposition:
            filename = part.get_filename()
            if filename:
                filename = _decode_header_value(filename)
                attachments.append({
                    "filename": filename,
                    "content": part.get_payload(decode=True) or b"",
                    "content_type": content_type,
                })
        elif content_type == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))

    return {
        "message_id": message_id,
        "subject": subject,
        "sender": sender,
        "date": date_tuple,
        "body": "\n".join(body_parts),
        "attachments": attachments,
    }


def _connect_imap(account: EmailAccount, password: str) -> imaplib.IMAP4_SSL | imaplib.IMAP4:
    """Stellt IMAP-Verbindung her."""
    if account.use_ssl:
        conn = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
    else:
        conn = imaplib.IMAP4(account.imap_host, account.imap_port)
    conn.login(account.username, password)
    return conn


async def test_imap_connection(account: EmailAccount, settings: Settings) -> dict:
    """Testet die IMAP-Verbindung. Returns {"success": bool, "error": str | None}."""
    try:
        key = settings.EMAIL_ENCRYPTION_KEY
        password = decrypt_password(account.encrypted_password, key)
        conn = _connect_imap(account, password)
        conn.select(account.folder_inbox, readonly=True)
        conn.close()
        conn.logout()
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def fetch_emails_for_account(
    account: EmailAccount,
    db: AsyncSession,
    settings: Settings,
) -> dict:
    """Ruft E-Mails fuer ein Konto ab und verarbeitet sie.

    Returns:
        {"total": int, "relevant": int, "irrelevant": int, "skipped": int, "failed": int}
    """
    stats = {"total": 0, "relevant": 0, "irrelevant": 0, "skipped": 0, "failed": 0}

    try:
        key = settings.EMAIL_ENCRYPTION_KEY
        password = decrypt_password(account.encrypted_password, key)
        conn = _connect_imap(account, password)
    except Exception as e:
        logger.error("IMAP-Verbindung fehlgeschlagen fuer %s: %s", account.name, e)
        account.last_error = str(e)
        account.last_checked_at = datetime.now(timezone.utc)
        await db.flush()
        return stats

    try:
        conn.select(account.folder_inbox)
        _status, msg_nums = conn.search(None, "UNSEEN")
        if not msg_nums or not msg_nums[0]:
            logger.info("Keine neuen E-Mails fuer %s", account.name)
            account.last_checked_at = datetime.now(timezone.utc)
            account.last_error = None
            await db.flush()
            conn.close()
            conn.logout()
            return stats

        num_list = msg_nums[0].split()
        stats["total"] = len(num_list)
        logger.info("%d neue E-Mails fuer %s", len(num_list), account.name)

        for num in num_list:
            try:
                _status, data = conn.fetch(num, "(RFC822)")
                raw = data[0][1] if isinstance(data[0], tuple) else data[0]
                parsed = parse_email_message(raw)

                # Duplikat-Check
                existing = await db.execute(
                    select(ProcessedEmail).where(
                        ProcessedEmail.email_account_id == account.id,
                        ProcessedEmail.message_id == parsed["message_id"],
                    )
                )
                if existing.scalar_one_or_none():
                    stats["skipped"] += 1
                    continue

                # LLM-Relevanzpruefung
                attachment_names = [a["filename"] for a in parsed["attachments"]]
                relevance = await check_email_relevance(
                    sender=parsed["sender"],
                    subject=parsed["subject"],
                    body_snippet=parsed["body"][:1000],
                    attachment_names=attachment_names,
                    settings=settings,
                )

                if not relevance["relevant"]:
                    processed = ProcessedEmail(
                        email_account_id=account.id,
                        message_id=parsed["message_id"],
                        subject=parsed["subject"],
                        sender=parsed["sender"],
                        received_at=parsed["date"],
                        status=EmailStatus.IRRELEVANT,
                        relevance_reason=relevance.get("reason", ""),
                    )
                    db.add(processed)
                    stats["irrelevant"] += 1
                    _move_email(conn, num, account.folder_processed)
                    continue

                # Relevant: Anhaenge + Body als Jobs einspeisen
                job_ids = await _create_jobs_from_email(
                    parsed, account, db, settings
                )

                processed = ProcessedEmail(
                    email_account_id=account.id,
                    message_id=parsed["message_id"],
                    subject=parsed["subject"],
                    sender=parsed["sender"],
                    received_at=parsed["date"],
                    status=EmailStatus.RELEVANT,
                    relevance_reason=relevance.get("reason", ""),
                    processing_job_id=job_ids[0] if job_ids else None,
                )
                db.add(processed)
                stats["relevant"] += 1
                _move_email(conn, num, account.folder_processed)

            except Exception:
                logger.exception("Fehler bei E-Mail %s", num)
                stats["failed"] += 1

        await db.flush()
        account.last_checked_at = datetime.now(timezone.utc)
        account.last_error = None
        await db.flush()

    except Exception as e:
        logger.exception("Fehler beim E-Mail-Abruf fuer %s", account.name)
        account.last_error = str(e)
        account.last_checked_at = datetime.now(timezone.utc)
        await db.flush()
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass

    return stats


def _move_email(conn: imaplib.IMAP4, msg_num: bytes, target_folder: str) -> None:
    """Verschiebt eine E-Mail in den Zielordner."""
    try:
        conn.copy(msg_num, target_folder)
        conn.store(msg_num, "+FLAGS", "\\Deleted")
    except Exception:
        logger.warning("E-Mail konnte nicht verschoben werden (Ordner %s existiert?)", target_folder)


async def _create_jobs_from_email(
    parsed: dict,
    account: EmailAccount,
    db: AsyncSession,
    settings: Settings,
) -> list[str]:
    """Erstellt ProcessingJobs fuer Anhaenge und ggf. E-Mail-Body."""
    from app.core.file_utils import generate_stored_filename

    job_ids = []
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Anhaenge als Jobs
    for att in parsed["attachments"]:
        filename = att["filename"]
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in settings.allowed_file_types_list:
            logger.info("Anhang %s uebersprungen (Typ %s nicht erlaubt)", filename, ext)
            continue

        stored_name = generate_stored_filename(filename)
        dest_path = upload_dir / stored_name
        dest_path.write_bytes(att["content"])

        job = ProcessingJob(
            original_filename=filename,
            stored_filename=stored_name,
            file_path=str(dest_path),
            file_type=ext,
            file_size_bytes=len(att["content"]),
            source=JobSource.EMAIL,
            status=JobStatus.PENDING,
            email_account_id=account.id,
        )
        db.add(job)
        await db.flush()
        job_ids.append(job.id)
        logger.info("E-Mail-Anhang als Job erstellt: %s -> %s", filename, job.id)

    # Body als .txt (wenn substanziell, > 100 Zeichen, und keine Anhaenge verarbeitet)
    body = parsed.get("body", "").strip()
    if body and len(body) > 100 and not job_ids:
        txt_filename = f"email_{parsed['subject'][:50]}.txt".replace("/", "_").replace("\\", "_")
        stored_name = generate_stored_filename(txt_filename)
        dest_path = upload_dir / stored_name
        dest_path.write_text(body, encoding="utf-8")

        job = ProcessingJob(
            original_filename=txt_filename,
            stored_filename=stored_name,
            file_path=str(dest_path),
            file_type="txt",
            file_size_bytes=len(body.encode("utf-8")),
            source=JobSource.EMAIL,
            status=JobStatus.PENDING,
            email_account_id=account.id,
        )
        db.add(job)
        await db.flush()
        job_ids.append(job.id)
        logger.info("E-Mail-Body als Job erstellt: %s -> %s", txt_filename, job.id)

    return job_ids
```

**Hinweis:** `txt` muss zu `ALLOWED_FILE_TYPES` hinzugefuegt werden, oder der Body-als-txt-Pfad muss die Validierung umgehen. Einfachste Loesung: `txt` zum Default hinzufuegen oder die Typ-Pruefung nur fuer Anhaenge machen (Body-Job braucht keine Extension-Pruefung da intern erzeugt).

Entscheidung: Body-Job umgeht die Extension-Pruefung (er wird intern generiert, nicht vom User hochgeladen). Der Code oben prueft `allowed_file_types_list` nur fuer Anhaenge.

### Step 3: Tests ausfuehren

Run: `cd backend && python -m pytest tests/services/test_email_fetch_service.py -v`
Expected: ALL PASS

### Step 4: Commit

```bash
git add backend/app/services/email_fetch_service.py \
  backend/tests/services/test_email_fetch_service.py
git commit -m "feat: E-Mail-Fetch-Service mit IMAP-Abruf + Job-Erstellung"
```

---

## Task 6: Pydantic Schemas fuer E-Mail-API

**Files:**
- Create: `backend/app/schemas/email.py`

### Step 1: Schemas erstellen

```python
"""Pydantic-Schemas fuer E-Mail-Konten und verarbeitete E-Mails."""

from datetime import datetime

from pydantic import BaseModel, Field


class EmailAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    imap_host: str = Field(..., min_length=1, max_length=500)
    imap_port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    username: str = Field(..., min_length=1, max_length=500)
    password: str = Field(..., min_length=1)  # Klartext, wird verschluesselt
    folder_inbox: str = Field(default="INBOX", max_length=500)
    folder_processed: str = Field(default="Zettelwirtschaft/Verarbeitet", max_length=500)
    schedule_type: str = Field(default="MANUAL")
    cron_expression: str | None = None
    filing_scope_id: int | None = None


class EmailAccountUpdate(BaseModel):
    name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    use_ssl: bool | None = None
    username: str | None = None
    password: str | None = None  # Nur wenn geaendert
    folder_inbox: str | None = None
    folder_processed: str | None = None
    schedule_type: str | None = None
    cron_expression: str | None = None
    is_active: bool | None = None
    filing_scope_id: int | None = None


class EmailAccountResponse(BaseModel):
    id: int
    name: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    folder_inbox: str
    folder_processed: str
    schedule_type: str
    cron_expression: str | None
    is_active: bool
    last_checked_at: datetime | None
    last_error: str | None
    filing_scope_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailTestResult(BaseModel):
    success: bool
    error: str | None = None


class EmailFetchResult(BaseModel):
    total: int
    relevant: int
    irrelevant: int
    skipped: int
    failed: int


class ProcessedEmailResponse(BaseModel):
    id: int
    message_id: str
    subject: str | None
    sender: str | None
    received_at: datetime | None
    status: str
    relevance_reason: str | None
    processing_job_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EmailStatsResponse(BaseModel):
    account_id: int
    account_name: str
    total_processed: int
    relevant: int
    irrelevant: int
    failed: int
    last_checked_at: datetime | None
```

### Step 2: Commit

```bash
git add backend/app/schemas/email.py
git commit -m "feat: Pydantic-Schemas fuer E-Mail-API"
```

---

## Task 7: API-Router fuer E-Mail-Konten

**Files:**
- Create: `backend/app/api/email.py`
- Modify: `backend/app/main.py` (Router registrieren)
- Create: `backend/tests/api/test_email.py`

### Step 1: Tests schreiben

Erstelle `backend/tests/api/test_email.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_list_accounts_empty(client):
    resp = await client.get("/api/email/accounts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_account(client):
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleQ==") as mock_gen, \
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
    assert "password" not in data  # Passwort nie in Response


@pytest.mark.asyncio
async def test_create_and_list_accounts(client):
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleQ=="), \
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
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleQ=="), \
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
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleQ=="), \
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
    with patch("app.api.email.generate_encryption_key", return_value="dGVzdGtleQ=="), \
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
async def test_get_history(client):
    resp = await client.get("/api/email/accounts/9999/history")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_stats(client):
    resp = await client.get("/api/email/stats")
    assert resp.status_code == 200
    assert resp.json() == []
```

### Step 2: Router implementieren

Erstelle `backend/app/api/email.py`:

```python
"""API-Router fuer E-Mail-Konten-Verwaltung."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.email_account import EmailAccount, ScheduleType
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.schemas.email import (
    EmailAccountCreate,
    EmailAccountResponse,
    EmailAccountUpdate,
    EmailFetchResult,
    EmailStatsResponse,
    EmailTestResult,
    ProcessedEmailResponse,
)
from app.services.crypto_service import decrypt_password, encrypt_password, generate_encryption_key
from app.services.email_fetch_service import fetch_emails_for_account, test_imap_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email", tags=["email"])


@router.get("/accounts", response_model=list[EmailAccountResponse])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).order_by(EmailAccount.name))
    return result.scalars().all()


@router.post("/accounts", response_model=EmailAccountResponse, status_code=201)
async def create_account(
    data: EmailAccountCreate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    key = settings.EMAIL_ENCRYPTION_KEY
    if not key:
        key = generate_encryption_key()
        logger.warning("EMAIL_ENCRYPTION_KEY nicht gesetzt - temporaerer Schluessel generiert. "
                       "Bitte in .env setzen: EMAIL_ENCRYPTION_KEY=%s", key)

    account = EmailAccount(
        name=data.name,
        imap_host=data.imap_host,
        imap_port=data.imap_port,
        use_ssl=data.use_ssl,
        username=data.username,
        encrypted_password=encrypt_password(data.password, key),
        folder_inbox=data.folder_inbox,
        folder_processed=data.folder_processed,
        schedule_type=ScheduleType(data.schedule_type),
        cron_expression=data.cron_expression,
        filing_scope_id=data.filing_scope_id,
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)
    return account


@router.put("/accounts/{account_id}", response_model=EmailAccountResponse)
async def update_account(
    account_id: int,
    data: EmailAccountUpdate,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "password" and value:
            key = settings.EMAIL_ENCRYPTION_KEY
            account.encrypted_password = encrypt_password(value, key)
        elif field == "schedule_type" and value:
            account.schedule_type = ScheduleType(value)
        elif field != "password":
            setattr(account, field, value)

    await db.flush()
    await db.refresh(account)
    return account


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")
    await db.delete(account)
    await db.flush()


@router.post("/accounts/{account_id}/test", response_model=EmailTestResult)
async def test_connection(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")
    return await test_imap_connection(account, settings)


@router.post("/accounts/{account_id}/fetch", response_model=EmailFetchResult)
async def manual_fetch(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Konto nicht gefunden")
    stats = await fetch_emails_for_account(account, db, settings)
    return stats


@router.get("/accounts/{account_id}/history", response_model=list[ProcessedEmailResponse])
async def get_history(
    account_id: int,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    # Account existiert?
    acc_result = await db.execute(select(EmailAccount).where(EmailAccount.id == account_id))
    if not acc_result.scalar_one_or_none():
        raise HTTPException(404, "Konto nicht gefunden")

    result = await db.execute(
        select(ProcessedEmail)
        .where(ProcessedEmail.email_account_id == account_id)
        .order_by(ProcessedEmail.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/stats", response_model=list[EmailStatsResponse])
async def get_stats(db: AsyncSession = Depends(get_db)):
    accounts = await db.execute(select(EmailAccount).order_by(EmailAccount.name))
    stats = []
    for account in accounts.scalars().all():
        total = await db.execute(
            select(func.count()).where(ProcessedEmail.email_account_id == account.id)
        )
        relevant = await db.execute(
            select(func.count()).where(
                ProcessedEmail.email_account_id == account.id,
                ProcessedEmail.status == EmailStatus.RELEVANT,
            )
        )
        irrelevant = await db.execute(
            select(func.count()).where(
                ProcessedEmail.email_account_id == account.id,
                ProcessedEmail.status == EmailStatus.IRRELEVANT,
            )
        )
        failed = await db.execute(
            select(func.count()).where(
                ProcessedEmail.email_account_id == account.id,
                ProcessedEmail.status == EmailStatus.FAILED,
            )
        )
        stats.append(EmailStatsResponse(
            account_id=account.id,
            account_name=account.name,
            total_processed=total.scalar() or 0,
            relevant=relevant.scalar() or 0,
            irrelevant=irrelevant.scalar() or 0,
            failed=failed.scalar() or 0,
            last_checked_at=account.last_checked_at,
        ))
    return stats
```

### Step 3: Router in main.py registrieren

In `backend/app/main.py`:

1. Import hinzufuegen (bei den anderen Router-Imports):
```python
from app.api.email import router as email_router
```

2. Router registrieren (nach `system_router`):
```python
app.include_router(email_router, prefix="/api")
```

### Step 4: Tests ausfuehren

Run: `cd backend && python -m pytest tests/api/test_email.py -v`
Expected: ALL PASS

### Step 5: Alle bestehenden Tests ausfuehren

Run: `cd backend && python -m pytest --tb=short -q`
Expected: Alle ~237 bestehenden Tests + neue Tests bestehen

### Step 6: Commit

```bash
git add backend/app/api/email.py backend/app/main.py \
  backend/app/schemas/email.py backend/tests/api/test_email.py
git commit -m "feat: E-Mail-API-Router mit CRUD + Test + Fetch + History + Stats"
```

---

## Task 8: E-Mail-Scheduler (Background-Task)

**Files:**
- Create: `backend/app/services/email_scheduler_service.py`
- Modify: `backend/app/main.py` (Scheduler als Background-Task starten)
- Create: `backend/tests/services/test_email_scheduler_service.py`

### Step 1: Tests schreiben

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from app.models.email_account import EmailAccount, ScheduleType
from app.services.email_scheduler_service import should_fetch_now


def test_should_fetch_cron_due():
    account = MagicMock()
    account.schedule_type = ScheduleType.CRON
    account.cron_expression = "*/15 * * * *"
    account.last_checked_at = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    # 16 Minuten spaeter -> faellig
    now = datetime(2026, 3, 3, 10, 16, 0, tzinfo=timezone.utc)
    assert should_fetch_now(account, now) is True


def test_should_fetch_cron_not_due():
    account = MagicMock()
    account.schedule_type = ScheduleType.CRON
    account.cron_expression = "*/15 * * * *"
    account.last_checked_at = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    # 5 Minuten spaeter -> nicht faellig
    now = datetime(2026, 3, 3, 10, 5, 0, tzinfo=timezone.utc)
    assert should_fetch_now(account, now) is False


def test_should_fetch_manual_never():
    account = MagicMock()
    account.schedule_type = ScheduleType.MANUAL
    assert should_fetch_now(account, datetime.now(timezone.utc)) is False


def test_should_fetch_never_checked():
    account = MagicMock()
    account.schedule_type = ScheduleType.CRON
    account.cron_expression = "*/15 * * * *"
    account.last_checked_at = None  # Noch nie geprueft
    assert should_fetch_now(account, datetime.now(timezone.utc)) is True
```

### Step 2: Implementieren

Erstelle `backend/app/services/email_scheduler_service.py`:

```python
"""Scheduler fuer automatischen E-Mail-Abruf."""

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.email_account import EmailAccount, ScheduleType
from app.services.email_fetch_service import fetch_emails_for_account

logger = logging.getLogger(__name__)

SCHEDULER_POLL_INTERVAL = 60  # Alle 60 Sekunden pruefen ob ein Konto faellig ist


def should_fetch_now(account, now: datetime) -> bool:
    """Prueft ob ein Konto jetzt abgerufen werden soll."""
    if account.schedule_type == ScheduleType.MANUAL:
        return False

    if account.schedule_type == ScheduleType.IDLE:
        # IDLE: immer abrufen wenn noch nie oder vor > 5 Minuten
        if not account.last_checked_at:
            return True
        diff = (now - account.last_checked_at).total_seconds()
        return diff >= 300

    if account.schedule_type == ScheduleType.CRON:
        if not account.cron_expression:
            return False
        if not account.last_checked_at:
            return True
        cron = croniter(account.cron_expression, account.last_checked_at)
        next_run = cron.get_next(datetime)
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        return now >= next_run

    return False


async def run_email_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Background-Task: prueft periodisch ob E-Mail-Konten abgerufen werden muessen."""
    logger.info("E-Mail-Scheduler gestartet (Intervall: %ds)", SCHEDULER_POLL_INTERVAL)

    while True:
        try:
            await asyncio.sleep(SCHEDULER_POLL_INTERVAL)
            now = datetime.now(timezone.utc)

            async with session_factory() as session:
                result = await session.execute(
                    select(EmailAccount).where(EmailAccount.is_active == True)  # noqa: E712
                )
                accounts = result.scalars().all()

                for account in accounts:
                    if should_fetch_now(account, now):
                        logger.info("Automatischer E-Mail-Abruf fuer: %s", account.name)
                        try:
                            stats = await fetch_emails_for_account(account, session, settings)
                            logger.info("Abruf %s: %s", account.name, stats)
                            await session.commit()
                        except Exception:
                            logger.exception("Fehler beim automatischen Abruf fuer %s", account.name)
                            await session.rollback()

        except asyncio.CancelledError:
            logger.info("E-Mail-Scheduler beendet")
            return
        except Exception:
            logger.exception("Unerwarteter Fehler im E-Mail-Scheduler")
            await asyncio.sleep(30)
```

### Step 3: In main.py als Background-Task registrieren

In `backend/app/main.py`, in der `lifespan`-Funktion, nach dem Auto-Backup-Task (vor `_initial_vectorize`):

```python
    # E-Mail-Scheduler
    from app.services.email_scheduler_service import run_email_scheduler

    email_task = asyncio.create_task(
        run_email_scheduler(async_session_factory, settings)
    )
    background_tasks.append(email_task)
```

### Step 4: Tests ausfuehren

Run: `cd backend && python -m pytest tests/services/test_email_scheduler_service.py -v`
Expected: ALL PASS

### Step 5: Commit

```bash
git add backend/app/services/email_scheduler_service.py \
  backend/app/main.py \
  backend/tests/services/test_email_scheduler_service.py
git commit -m "feat: E-Mail-Scheduler Background-Task mit CRON/IDLE-Support"
```

---

## Task 9: Frontend — E-Mail-Einstellungen in SettingsView

**Files:**
- Create: `frontend/src/components/email/EmailAccountForm.vue`
- Create: `frontend/src/components/email/EmailAccountList.vue`
- Modify: `frontend/src/views/SettingsView.vue` (neuer Tab/Abschnitt)
- Modify: `frontend/src/services/api.js` (E-Mail-API-Funktionen)

### Step 1: API-Client erweitern

In `frontend/src/services/api.js` — neue Funktionen hinzufuegen:

```javascript
// E-Mail-Konten
export const getEmailAccounts = () => api.get('/email/accounts')
export const createEmailAccount = (data) => api.post('/email/accounts', data)
export const updateEmailAccount = (id, data) => api.put(`/email/accounts/${id}`, data)
export const deleteEmailAccount = (id) => api.delete(`/email/accounts/${id}`)
export const testEmailConnection = (id) => api.post(`/email/accounts/${id}/test`)
export const fetchEmailsNow = (id) => api.post(`/email/accounts/${id}/fetch`)
export const getEmailHistory = (id, params) => api.get(`/email/accounts/${id}/history`, { params })
export const getEmailStats = () => api.get('/email/stats')
```

### Step 2: EmailAccountList.vue erstellen

Erstelle `frontend/src/components/email/EmailAccountList.vue`:

Zeigt alle E-Mail-Konten als Karten. Pro Konto:
- Name, Host, Username
- Status (aktiv/inaktiv), letzter Abruf, letzter Fehler
- Buttons: Bearbeiten, Testen, Jetzt abrufen, Loeschen
- Abruf-Ergebnis als Toast anzeigen

Vue 3 Composition API mit `<script setup>`, TailwindCSS.

### Step 3: EmailAccountForm.vue erstellen

Erstelle `frontend/src/components/email/EmailAccountForm.vue`:

Modal-Formular fuer Konto anlegen/bearbeiten:
- Name, IMAP-Host, Port, SSL-Toggle
- Username, Passwort (password input)
- Posteingang-Ordner, Verarbeitet-Ordner
- Schedule-Typ (Dropdown: Manuell/CRON/IDLE)
- CRON-Ausdruck (nur sichtbar bei CRON, mit Hilfetext)
- Ablagebereich (Dropdown, optional)

### Step 4: SettingsView.vue erweitern

Neuer Abschnitt "E-Mail-Konten" in SettingsView.vue:
- EmailAccountList-Komponente einbinden
- "Konto hinzufuegen"-Button oeffnet EmailAccountForm

### Step 5: Commit

```bash
git add frontend/src/components/email/ frontend/src/views/SettingsView.vue \
  frontend/src/services/api.js
git commit -m "feat: Frontend E-Mail-Konten-Verwaltung in Einstellungen"
```

---

## Task 10: Frontend — E-Mail-Historie + Dashboard-Karte

**Files:**
- Create: `frontend/src/components/email/EmailHistory.vue`
- Modify: `frontend/src/views/DashboardView.vue`

### Step 1: EmailHistory.vue

Zeigt verarbeitete E-Mails pro Konto als Tabelle:
- Betreff, Absender, Datum, Status (RELEVANT/IRRELEVANT/FAILED als Badge), Grund
- Paginierung

### Step 2: Dashboard-Karte

In DashboardView.vue — neue StatCard (nur sichtbar wenn E-Mail-Konten konfiguriert):
- Icon: Briefumschlag
- Zaehler: "X E-Mails geprueft, Y importiert"
- Daten via `getEmailStats()`

### Step 3: Commit

```bash
git add frontend/src/components/email/EmailHistory.vue \
  frontend/src/views/DashboardView.vue
git commit -m "feat: E-Mail-Historie + Dashboard-Statistik-Karte"
```

---

## Task 11: CLAUDE.md + MEMORY.md aktualisieren

**Files:**
- Modify: `CLAUDE.md`
- Modify: `C:\Users\manue\.claude\projects\E--claude-zettelwirtschaft\memory\MEMORY.md`

### Step 1: CLAUDE.md

- Projektstruktur ergaenzen: `email_account.py`, `processed_email.py`, `email_fetch_service.py`, `email_relevance_service.py`, `email_scheduler_service.py`, `crypto_service.py`, `email_relevance.txt`, Schemas, API-Router, Frontend-Komponenten
- Architektur-Entscheidungen ergaenzen: IMAP-Polling, Fernet-Verschluesselung, LLM-Relevanzpruefung
- Datenmodelle ergaenzen: EmailAccount, ProcessedEmail, ScheduleType, EmailStatus
- Implementierungsstatus: E-Mail-Anbindung (Issue #18) als done markieren
- Migration 009 dokumentieren

### Step 2: Commit

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md um E-Mail-Anbindung erweitern"
```

---

## Task 12: Alle Tests + Frontend Build ausfuehren

### Step 1: Backend-Tests

Run: `cd backend && python -m pytest --tb=short -q`
Expected: Alle Tests bestehen (bestehende ~237 + ~20 neue)

### Step 2: Frontend-Build

Run: `cd frontend && npm run build`
Expected: Build erfolgreich ohne Fehler

### Step 3: Finaler Commit

```bash
git add -A
git commit -m "feat: E-Mail-Anbindung komplett (Issue #18)

- IMAP-Polling fuer mehrere E-Mail-Konten
- LLM-Relevanzpruefung (archivierungswuerdige Dokumente erkennen)
- Fernet-verschluesselte Passwoerter
- Scheduling: CRON, Manuell, IDLE
- Web-UI: Konten-Verwaltung, Verbindungstest, Historie
- Dashboard: E-Mail-Statistik-Karte
- Migration 009, neue Models, API, Tests

Closes #18"
```
