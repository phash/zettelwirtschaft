"""E-Mail-Abruf und -Verarbeitung via IMAP."""

import asyncio
import email
import email.utils
import imaplib
import logging
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
    """Parst eine rohe E-Mail in strukturierte Daten."""
    msg = email.message_from_bytes(raw_bytes)

    message_id = msg.get("Message-ID", "")
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    date_tuple = None
    if msg.get("Date"):
        try:
            date_tuple = email.utils.parsedate_to_datetime(msg.get("Date"))
        except Exception:
            pass

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
        # Cleartext IMAP — Passwort fliesst unverschluesselt durchs LAN.
        # Auf Heim-Routern selten ausnutzbar, aber definitiv unschoen — pro
        # Verbindung warnen damit der User es im Log mitbekommt.
        logger.warning(
            "IMAP-Verbindung zu %s:%d ohne SSL — Login-Daten gehen im Klartext "
            "uebers Netzwerk. Bitte 993/IMAPS nutzen.",
            account.imap_host, account.imap_port,
        )
        conn = imaplib.IMAP4(account.imap_host, account.imap_port)
    conn.login(account.username, password)
    return conn


def _test_imap_sync(account: EmailAccount, password: str) -> dict:
    """Synchroner IMAP-Verbindungstest."""
    conn = _connect_imap(account, password)
    conn.select(account.folder_inbox, readonly=True)
    conn.close()
    conn.logout()
    return {"success": True, "error": None}


async def test_imap_connection(account: EmailAccount, settings: Settings) -> dict:
    """Testet die IMAP-Verbindung (non-blocking)."""
    try:
        key = settings.EMAIL_ENCRYPTION_KEY
        password = decrypt_password(account.encrypted_password, key)
        return await asyncio.to_thread(_test_imap_sync, account, password)
    except Exception as e:
        return {"success": False, "error": str(e)}


def _fetch_raw_emails_sync(account: EmailAccount, password: str) -> list[tuple[bytes, bytes]]:
    """Synchroner IMAP-Abruf: gibt Liste von (msg_num, raw_bytes) zurueck."""
    conn = _connect_imap(account, password)
    try:
        conn.select(account.folder_inbox)
        _status, msg_nums = conn.search(None, "UNSEEN")
        if not msg_nums or not msg_nums[0]:
            return []

        results = []
        for num in msg_nums[0].split():
            _status, data = conn.fetch(num, "(RFC822)")
            raw = data[0][1] if isinstance(data[0], tuple) else data[0]
            results.append((num, raw))
        return results
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass


def _move_emails_sync(account: EmailAccount, password: str, msg_nums: list[bytes], target_folder: str) -> None:
    """Synchroner IMAP-Move: verschiebt E-Mails in Zielordner."""
    if not msg_nums:
        return
    conn = _connect_imap(account, password)
    try:
        conn.select(account.folder_inbox)
        for num in msg_nums:
            _move_email(conn, num, target_folder)
        # Erst nach EXPUNGE sind die als Deleted markierten Mails wirklich weg.
        # Ohne EXPUNGE werden sie bei manchen IMAP-Servern (Gmail / Dovecot mit
        # auto-expunge=off) nach Reconnect erneut als UNSEEN sichtbar -> doppelte
        # LLM-Relevance-Calls.
        try:
            conn.expunge()
        except Exception:
            logger.warning("EXPUNGE fehlgeschlagen fuer %s", account.name)
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass


async def fetch_emails_for_account(
    account: EmailAccount,
    db: AsyncSession,
    settings: Settings,
) -> dict:
    """Ruft E-Mails fuer ein Konto ab und verarbeitet sie (non-blocking)."""
    stats = {"total": 0, "relevant": 0, "irrelevant": 0, "skipped": 0, "failed": 0}

    try:
        key = settings.EMAIL_ENCRYPTION_KEY
        password = decrypt_password(account.encrypted_password, key)
    except Exception as e:
        logger.error("Passwort-Entschluesselung fehlgeschlagen fuer %s: %s", account.name, e)
        account.last_error = str(e)
        account.last_checked_at = datetime.now(timezone.utc)
        await db.flush()
        return stats

    # IMAP-Abruf in Thread (blocking I/O)
    try:
        raw_emails = await asyncio.to_thread(_fetch_raw_emails_sync, account, password)
    except Exception as e:
        logger.error("IMAP-Verbindung fehlgeschlagen fuer %s: %s", account.name, e)
        account.last_error = str(e)
        account.last_checked_at = datetime.now(timezone.utc)
        await db.flush()
        return stats

    if not raw_emails:
        logger.info("Keine neuen E-Mails fuer %s", account.name)
        account.last_checked_at = datetime.now(timezone.utc)
        account.last_error = None
        await db.flush()
        return stats

    stats["total"] = len(raw_emails)
    logger.info("%d neue E-Mails fuer %s", len(raw_emails), account.name)

    emails_to_move: list[bytes] = []

    for num, raw in raw_emails:
        try:
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
                emails_to_move.append(num)
                continue

            # Relevant: Anhaenge + Body als Jobs einspeisen
            job_ids = await _create_jobs_from_email(parsed, account, db, settings)

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
            emails_to_move.append(num)

        except Exception:
            logger.exception("Fehler bei E-Mail %s", num)
            stats["failed"] += 1

    await db.flush()
    account.last_checked_at = datetime.now(timezone.utc)
    account.last_error = None
    await db.flush()

    # Verarbeitete E-Mails verschieben (in Thread)
    try:
        await asyncio.to_thread(
            _move_emails_sync, account, password, emails_to_move, account.folder_processed
        )
    except Exception:
        logger.warning("E-Mails konnten nicht verschoben werden fuer %s", account.name)

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

    from app.core.file_utils import MAGIC_BYTES

    for att in parsed["attachments"]:
        filename = att["filename"]
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in settings.allowed_file_types_list:
            logger.info("Anhang %s uebersprungen (Typ %s nicht erlaubt)", filename, ext)
            continue

        # Magic-Byte-Validierung (wie bei manuellem Upload)
        content = att["content"]
        signatures = MAGIC_BYTES.get(ext)
        if signatures and not any(content.startswith(sig) for sig in signatures):
            logger.warning("Anhang %s: Magic-Byte-Validierung fehlgeschlagen, uebersprungen", filename)
            continue

        stored_name = generate_stored_filename(filename)
        dest_path = upload_dir / stored_name
        dest_path.write_bytes(content)

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

    # F-07: Body als .txt-File, aber ocr_text vorab am Job gesetzt. Damit
    # wird OCR-Tesseract uebersprungen (Body ist bereits Text), und der
    # file_type="txt"-Pfad geht durch keine ALLOWED_FILE_TYPES-Schwelle die
    # ausserhalb der intern erzeugten E-Mail-Quelle relevant waere.
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
            # F-07: Body ist bereits sauberer Text — OCR ueberspringen,
            # Konfidenz 1.0 (keine Erkennungs-Unsicherheit).
            ocr_text=body,
            ocr_confidence=1.0,
        )
        db.add(job)
        await db.flush()
        job_ids.append(job.id)
        logger.info("E-Mail-Body als Job erstellt: %s -> %s", txt_filename, job.id)

    return job_ids
