"""E-Mail-Abruf und -Verarbeitung via IMAP.

Ablauf pro Abruf (``fetch_emails_for_account``):

1. **Abholen** (eigene IMAP-Verbindung, Ordner read-only/EXAMINE): ``UID SEARCH
   UNSEEN``, aelteste ``EMAIL_MAX_PER_RUN`` Mails per ``UID FETCH BODY.PEEK[]``
   — PEEK setzt *kein* ``\\Seen``. Bewusste Design-Entscheidung: importiert
   werden nur ungelesene E-Mails.
2. **Verarbeiten** (ohne IMAP-Verbindung): pro Mail eine eigene kurze
   DB-Transaktion (Duplikat-Check, LLM-Relevanz, Jobs, ProcessedEmail). Keine
   Schreib-Transaktion ueber LLM-Aufrufe (SQLite-Writer-Lock).
3. **Ablegen** (neue Verbindung, read-write): nur fuer bereits committete Mails,
   per UID und nach UIDVALIDITY-Pruefung. Zielordner wird bei Bedarf angelegt;
   ``UID MOVE`` (MOVE-Capability) oder ``UID COPY`` -> nur bei OK ``\\Deleted``
   + ``UID EXPUNGE`` genau dieser UIDs. Nie ein pauschales EXPUNGE/CLOSE (das
   wuerde auch vom Benutzer als geloescht markierte Mails entfernen). Laesst
   sich der Zielordner nicht anlegen, werden die Mails nur als gelesen markiert.

Bricht ein Lauf zwischen 2 und 3 ab, bleiben die Mails ungelesen im Posteingang;
der naechste Lauf erkennt sie als Duplikat und legt sie dann ab.
"""

import asyncio
import email
import email.errors
import email.utils
import hashlib
import imaplib
import logging
import mimetypes
import re
import socket
import ssl
import weakref
from base64 import b64encode
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from html.parser import HTMLParser
from pathlib import Path

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.file_utils import generate_stored_filename
from app.models.email_account import EmailAccount
from app.models.processed_email import EmailStatus, ProcessedEmail
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.services.crypto_service import decrypt_password
from app.services.email_relevance_service import check_email_relevance
from app.services.file_validation_service import (
    FileValidationError,
    validate_file,
    validate_file_metadata,
)
from app.services.notification_service import notify_system
from app.services.upload_service import create_job

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
ProgressCallback = Callable[[int | None, str | None], Awaitable[None]]

# Gemeinsame Meldung fuer fehlenden/ungueltigen Schluessel (API-503 + last_error).
ENCRYPTION_KEY_MISSING_MESSAGE = (
    "EMAIL_ENCRYPTION_KEY ist nicht konfiguriert. Bitte in der .env (Docker) "
    "bzw. config.toml (Windows-Installation) setzen und den Dienst neu starten."
)
_DECRYPT_FAILED_MESSAGE = (
    "Passwort kann nicht entschlüsselt werden (EMAIL_ENCRYPTION_KEY geändert?) "
    "— bitte Passwort neu eingeben."
)
_CONNECTION_HINT = "Host, Port, SSL und Zugangsdaten prüfen."

# Untere Grenze fuer einen E-Mail-Text, der ohne Anhang als Dokument taugt.
_MIN_BODY_DOCUMENT_CHARS = 100
_MAX_REASON_LEN = 500
_MAX_MESSAGE_ID_LEN = 1000


# ---------------------------------------------------------------------------
# Fehler + Meldungen
# ---------------------------------------------------------------------------


class EmailFetchError(Exception):
    """Erwarteter Fehler mit fester deutscher Meldung fuer die UI."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def classify_imap_error(exc: BaseException) -> str:
    """Uebersetzt Verbindungs-/IMAP-Fehler in eine feste deutsche Meldung.

    S-LOW4: Rohe Exception-Texte (interne Hosts, Ports, Server-Banner) bleiben
    im Server-Log; die UI bekommt nur die Kategorie.
    """
    if isinstance(exc, EmailFetchError):
        return exc.message
    if isinstance(exc, socket.gaierror):
        return "Server nicht gefunden — Hostname prüfen."
    if isinstance(exc, TimeoutError):
        return "Zeitüberschreitung — Server antwortet nicht (Port/SSL prüfen)."
    if isinstance(exc, ssl.SSLError):
        return "TLS/SSL-Fehler — Port und SSL-Einstellung prüfen."
    if isinstance(exc, ConnectionRefusedError):
        return "Verbindung abgelehnt — Host und Port prüfen."
    if isinstance(exc, imaplib.IMAP4.abort):
        return "Verbindung vom Server unterbrochen — später erneut versuchen."
    if isinstance(exc, imaplib.IMAP4.error):
        return "IMAP-Server hat einen Befehl abgelehnt — Details im Server-Log."
    if isinstance(exc, OSError):
        return "Server nicht erreichbar — Host, Port und Netzwerk prüfen."
    return "Unerwarteter Fehler beim E-Mail-Abruf — Details im Server-Log."


def _decrypt_account_password(encrypted_password: str, settings: Settings) -> str:
    """Entschluesselt das Konto-Passwort oder wirft EmailFetchError."""
    key = settings.EMAIL_ENCRYPTION_KEY
    if not key:
        raise EmailFetchError(ENCRYPTION_KEY_MISSING_MESSAGE)
    try:
        return decrypt_password(encrypted_password, key)
    except InvalidToken:
        # str(InvalidToken()) ist "" — ohne eigene Meldung waere last_error leer.
        raise EmailFetchError(_DECRYPT_FAILED_MESSAGE) from None
    except (ValueError, TypeError):
        # Fernet(key) mit ungueltigem Schluessel-Format.
        raise EmailFetchError(ENCRYPTION_KEY_MISSING_MESSAGE) from None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _safe_decode(data: bytes, charset: str | None) -> str:
    """Dekodiert Bytes; unbekannte Charsets ('unknown-8bit', 'x-unknown') -> UTF-8."""
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _decode_header_value(value) -> str:
    """Dekodiert MIME-kodierte Header-Werte (RFC 2047) robust."""
    if not value:
        return ""
    try:
        # make_header setzt die Leerzeichen zwischen Encoded-Words korrekt.
        return str(make_header(decode_header(str(value)))).strip()
    except (LookupError, UnicodeError, ValueError, email.errors.HeaderParseError):
        pass
    try:
        parts = decode_header(str(value))
    except (UnicodeError, ValueError, email.errors.HeaderParseError):
        return str(value).strip()
    decoded = []
    for part, charset in parts:
        decoded.append(_safe_decode(part, charset) if isinstance(part, bytes) else part)
    return "".join(decoded).strip()


def _surrogate_message_id(raw_bytes: bytes) -> str:
    """Stabiler Ersatz-Identifier fuer Mails ohne (lesbare) Message-ID."""
    return "noid-" + hashlib.sha256(raw_bytes).hexdigest()


class _HtmlTextExtractor(HTMLParser):
    """Minimaler HTML->Text-Konverter fuer HTML-only-Mails."""

    _SKIP = {"script", "style", "head", "title"}
    _BLOCK = {
        "br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "ul", "ol", "section", "article", "header", "footer", "hr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")
        elif tag in ("td", "th"):
            self._chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        # Entities sind schon aufgeloest (convert_charrefs=True) — kein zweites
        # unescape, sonst wuerde aus "&amp;lt;" ein "<".
        raw = "".join(self._chunks)
        lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in raw.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _html_to_text(html: str) -> str:
    parser = _HtmlTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # HTMLParser ist tolerant; reine Absicherung
        logger.debug("HTML-Konvertierung abgebrochen", exc_info=True)
    return parser.text()


def _fallback_attachment_name(content_type: str) -> str:
    ext = mimetypes.guess_extension(content_type) or ".bin"
    return f"anhang{ext}"


def parse_email_message(raw_bytes: bytes) -> dict:
    """Parst eine rohe E-Mail in strukturierte Daten."""
    msg = email.message_from_bytes(raw_bytes)

    message_id = _decode_header_value(msg.get("Message-ID"))[:_MAX_MESSAGE_ID_LEN]
    if not message_id:
        # Ohne Message-ID wuerden alle ID-losen Mails auf '' kollidieren und
        # nach der ersten als Duplikat uebersprungen.
        message_id = _surrogate_message_id(raw_bytes)
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    date_tuple = None
    if msg.get("Date"):
        try:
            date_tuple = email.utils.parsedate_to_datetime(str(msg.get("Date")))
        except (TypeError, ValueError, IndexError):
            pass

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()  # 'attachment' | 'inline' | None
        filename = part.get_filename()  # Content-Disposition filename ODER Content-Type name

        if filename or disposition == "attachment":
            # Eingebettete Inline-Bilder mit Content-ID (Logos, Signaturen)
            # sind keine Dokumente.
            if (
                disposition == "inline"
                and part.get("Content-ID")
                and part.get_content_maintype() == "image"
            ):
                continue
            name = _decode_header_value(filename) if filename else ""
            attachments.append({
                "filename": name or _fallback_attachment_name(content_type),
                "content": part.get_payload(decode=True) or b"",
                "content_type": content_type,
            })
        elif content_type in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload:
                text = _safe_decode(payload, part.get_content_charset())
                (plain_parts if content_type == "text/plain" else html_parts).append(text)

    if plain_parts:
        body = "\n".join(plain_parts)
    else:
        body = "\n\n".join(_html_to_text(h) for h in html_parts)

    return {
        "message_id": message_id,
        "subject": subject,
        "sender": sender,
        "date": date_tuple,
        "body": body,
        "attachments": attachments,
    }


# ---------------------------------------------------------------------------
# IMAP-Hilfsfunktionen (synchron, laufen in asyncio.to_thread)
# ---------------------------------------------------------------------------


def _encode_mailbox(name: str) -> str:
    """Mailbox-Name als IMAP-quoted-String in modified UTF-7 (RFC 3501 5.1.3)."""
    out: list[str] = []
    pending: list[str] = []

    def flush():
        if pending:
            utf16 = "".join(pending).encode("utf-16-be")
            out.append("&" + b64encode(utf16).decode("ascii").rstrip("=").replace("/", ",") + "-")
            pending.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            pending.append(ch)
    flush()
    encoded = "".join(out)
    return '"' + encoded.replace("\\", "\\\\").replace('"', '\\"') + '"'


_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>NIL|"(?:[^"\\]|\\.)*")\s')


def _ok(typ) -> bool:
    return typ == "OK"


def _list_entries(data) -> list[bytes]:
    """LIST-Antwortzeilen ohne None-/Literal-Fragmente."""
    entries = []
    for item in data or []:
        if isinstance(item, tuple):
            item = item[0]
        if isinstance(item, bytes) and item:
            entries.append(item)
    return entries


def _hierarchy_delimiter(conn: imaplib.IMAP4) -> str | None:
    typ, data = conn.list('""', '""')
    if not _ok(typ):
        return None
    for entry in _list_entries(data):
        match = _LIST_RE.match(entry)
        if match and match.group("delim") != b"NIL":
            # Quoted-String entschluesseln: Wire-Form "\\" -> ein einzelner Backslash als Trenner.
            delim = re.sub(rb"\\(.)", rb"\1", match.group("delim")[1:-1])
            return delim.decode("ascii", "replace") or None
    return None


def _server_folder_name(conn: imaplib.IMAP4, folder: str) -> str:
    """Konfigurierten Ordnerpfad ('A/B') auf den Server-Trenner abbilden."""
    delim = _hierarchy_delimiter(conn)
    if delim and delim != "/":
        return folder.replace("/", delim)
    return folder


def _folder_exists(conn: imaplib.IMAP4, quoted: str) -> bool:
    typ, data = conn.list('""', quoted)
    return _ok(typ) and bool(_list_entries(data))


def _ensure_folder(conn: imaplib.IMAP4, folder: str) -> str | None:
    """Stellt sicher, dass ``folder`` existiert. Gibt den quoted Namen oder None."""
    quoted = _encode_mailbox(_server_folder_name(conn, folder))
    if _folder_exists(conn, quoted):
        return quoted
    typ, _ = conn.create(quoted)
    if not _ok(typ) and not _folder_exists(conn, quoted):
        return None
    try:
        conn.subscribe(quoted)  # damit der Ordner in Mail-Clients sichtbar ist
    except Exception:
        logger.debug("SUBSCRIBE fuer %s fehlgeschlagen", folder, exc_info=True)
    return quoted


def _capabilities(conn: imaplib.IMAP4) -> set[str]:
    """Capabilities NACH dem Login (koennen sich gegenueber vorher aendern)."""
    try:
        typ, data = conn.capability()
        if _ok(typ) and data and data[0]:
            return {c.upper() for c in data[0].decode("ascii", "replace").split()}
    except Exception:
        logger.debug("CAPABILITY fehlgeschlagen", exc_info=True)
    return {str(c).upper() for c in getattr(conn, "capabilities", ())}


def _uid_validity(conn: imaplib.IMAP4) -> str | None:
    _name, data = conn.response("UIDVALIDITY")
    if data and data[0]:
        value = data[0]
        return value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)
    return None


def _select(conn: imaplib.IMAP4, folder: str, *, readonly: bool) -> None:
    typ, _ = conn.select(_encode_mailbox(folder), readonly=readonly)
    if not _ok(typ):
        raise EmailFetchError(f"Postfach-Ordner „{folder}“ nicht gefunden.")


def _logout(conn: imaplib.IMAP4) -> None:
    # Bewusst KEIN close(): CLOSE expungiert implizit alle \Deleted-Mails des
    # Ordners — auch solche, die der Benutzer selbst markiert hat.
    try:
        conn.logout()
    except Exception:
        logger.debug("IMAP-LOGOUT fehlgeschlagen", exc_info=True)


def _connect_imap(account, password: str, timeout: float) -> imaplib.IMAP4:
    """Stellt eine IMAP-Verbindung mit Socket-Timeout her und meldet an."""
    if account.use_ssl:
        conn = imaplib.IMAP4_SSL(account.imap_host, account.imap_port, timeout=timeout)
    else:
        # Cleartext IMAP — Passwort fliesst unverschluesselt durchs LAN.
        # Auf Heim-Routern selten ausnutzbar, aber definitiv unschoen — pro
        # Verbindung warnen damit der User es im Log mitbekommt.
        logger.warning(
            "IMAP-Verbindung zu %s:%d ohne SSL — Login-Daten gehen im Klartext "
            "uebers Netzwerk. Bitte 993/IMAPS nutzen.",
            account.imap_host, account.imap_port,
        )
        conn = imaplib.IMAP4(account.imap_host, account.imap_port, timeout=timeout)
    try:
        conn.login(account.username, password)
    except imaplib.IMAP4.abort:
        _logout(conn)
        raise
    except imaplib.IMAP4.error:
        _logout(conn)
        raise EmailFetchError("Anmeldung fehlgeschlagen — Benutzername und Passwort prüfen.") from None
    return conn


@dataclass(frozen=True)
class _AccountSnapshot:
    """Primitive Konto-Daten — kein ORM-Objekt ueber Sessions/Threads hinweg."""

    id: int
    name: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    encrypted_password: str
    folder_inbox: str
    folder_processed: str

    @classmethod
    def of(cls, account: EmailAccount) -> "_AccountSnapshot":
        return cls(
            id=account.id,
            name=account.name,
            imap_host=account.imap_host,
            imap_port=account.imap_port,
            use_ssl=account.use_ssl,
            username=account.username,
            encrypted_password=account.encrypted_password,
            folder_inbox=account.folder_inbox,
            folder_processed=account.folder_processed,
        )


@dataclass
class _FetchBatch:
    uid_validity: str | None
    messages: list[tuple[bytes, bytes]]  # (uid, raw)
    remaining: int


def _fetch_raw_emails_sync(
    account: _AccountSnapshot, password: str, timeout: float, limit: int
) -> _FetchBatch:
    """Holt die aeltesten ``limit`` ungelesenen Mails, ohne Flags zu aendern."""
    conn = _connect_imap(account, password, timeout)
    try:
        _select(conn, account.folder_inbox, readonly=True)
        uid_validity = _uid_validity(conn)
        typ, data = conn.uid("SEARCH", "UNSEEN")
        if not _ok(typ):
            raise EmailFetchError("Suche nach ungelesenen E-Mails fehlgeschlagen.")
        uids = data[0].split() if data and data[0] else []
        # UIDs steigen mit dem Eingang -> aufsteigend sortiert = aelteste zuerst.
        uids.sort(key=int)
        batch, rest = uids[:limit], uids[limit:]

        messages = []
        for uid in batch:
            typ, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next(
                (item[1] for item in fetched or [] if isinstance(item, tuple) and len(item) > 1),
                None,
            )
            if not _ok(typ) or raw is None:
                logger.warning("E-Mail UID %r von %s konnte nicht geladen werden", uid, account.name)
                continue
            messages.append((uid, raw))
        return _FetchBatch(uid_validity=uid_validity, messages=messages, remaining=len(rest))
    finally:
        _logout(conn)


def _uid_set(uids: list[bytes]) -> str:
    return ",".join(u.decode("ascii") if isinstance(u, bytes) else str(u) for u in uids)


def _file_emails_sync(
    account: _AccountSnapshot,
    password: str,
    timeout: float,
    uids: list[bytes],
    uid_validity: str | None,
) -> str | None:
    """Legt verarbeitete Mails ab. Gibt eine Warnung (deutsch) oder None zurueck."""
    if not uids:
        return None
    conn = _connect_imap(account, password, timeout)
    try:
        _select(conn, account.folder_inbox, readonly=False)
        current_validity = _uid_validity(conn)
        if uid_validity is not None and current_validity != uid_validity:
            # UIDs gelten nur innerhalb derselben UIDVALIDITY — sonst koennte es
            # die falschen Mails treffen. Naechster Lauf holt sie als Duplikat.
            logger.warning(
                "UIDVALIDITY von %s hat sich geaendert (%s -> %s) — Ablage uebersprungen",
                account.name, uid_validity, current_validity,
            )
            return "Postfach wurde zwischenzeitlich verändert — E-Mails werden beim nächsten Abruf abgelegt."

        uid_set = _uid_set(uids)
        target = _ensure_folder(conn, account.folder_processed)
        if target is None:
            logger.warning(
                "Zielordner %r fuer %s konnte nicht angelegt werden — markiere nur als gelesen",
                account.folder_processed, account.name,
            )
            conn.uid("STORE", uid_set, "+FLAGS", "(\\Seen)")
            return (
                f"Zielordner „{account.folder_processed}“ konnte nicht angelegt werden — "
                "verarbeitete E-Mails wurden nur als gelesen markiert."
            )

        caps = _capabilities(conn)
        if "MOVE" in caps:
            typ, _ = conn.uid("MOVE", uid_set, target)
            if _ok(typ):
                return None
            logger.warning("UID MOVE fuer %s abgelehnt — versuche COPY", account.name)

        typ, _ = conn.uid("COPY", uid_set, target)
        if not _ok(typ):
            # Ohne erfolgreiche Kopie NIEMALS \Deleted setzen — sonst Datenverlust.
            logger.warning("UID COPY nach %r fuer %s abgelehnt", account.folder_processed, account.name)
            conn.uid("STORE", uid_set, "+FLAGS", "(\\Seen)")
            return (
                f"E-Mails konnten nicht nach „{account.folder_processed}“ verschoben werden — "
                "sie wurden nur als gelesen markiert."
            )
        conn.uid("STORE", uid_set, "+FLAGS", "(\\Seen \\Deleted)")
        if "UIDPLUS" in caps:
            conn.uid("EXPUNGE", uid_set)
        # Ohne UIDPLUS gibt es kein gezieltes Expunge; die Kopie liegt im
        # Zielordner, das Original bleibt als gelesen+geloescht markiert, bis
        # der Mail-Client den Ordner bereinigt.
        return None
    finally:
        _logout(conn)


def _test_imap_sync(account, password: str, timeout: float) -> dict:
    """Synchroner IMAP-Verbindungstest inkl. Ordner-Pruefung."""
    conn = _connect_imap(account, password, timeout)
    try:
        _select(conn, account.folder_inbox, readonly=True)
        quoted = _encode_mailbox(_server_folder_name(conn, account.folder_processed))
        warning = None
        if not _folder_exists(conn, quoted):
            warning = (
                f"Zielordner „{account.folder_processed}“ existiert noch nicht — "
                "er wird beim ersten Abruf angelegt."
            )
        return {"success": True, "error": None, "warning": warning}
    finally:
        _logout(conn)


async def test_imap_connection(account: EmailAccount, settings: Settings) -> dict:
    """Testet die IMAP-Verbindung (non-blocking)."""
    snapshot = _AccountSnapshot.of(account)
    try:
        password = _decrypt_account_password(snapshot.encrypted_password, settings)
        return await asyncio.to_thread(_test_imap_sync, snapshot, password, settings.IMAP_TIMEOUT)
    except Exception as e:
        # S-LOW4: rohen Verbindungsfehler nur serverseitig loggen; dem Client eine
        # klassifizierte, feste Meldung geben (kein praeziser Recon-Oracle ueber
        # interne Hosts/Ports, wenn PIN aus ist). Bewusst KEIN IP-Denylist —
        # interne LAN-Mailserver (NAS o.ae.) sind ein legitimer On-Prem-Fall.
        logger.warning("IMAP-Verbindungstest fehlgeschlagen fuer %s: %r", snapshot.imap_host, e)
        message = classify_imap_error(e)
        if not isinstance(e, EmailFetchError):
            message = f"{message} {_CONNECTION_HINT}"
        return {"success": False, "error": message, "warning": None}


# ---------------------------------------------------------------------------
# Konto-Sperre (geteilt zwischen Scheduler und manuellem Abruf)
# ---------------------------------------------------------------------------

# Pro Event-Loop eigene Locks (asyncio.Lock bindet sich an einen Loop; Tests
# laufen mit einem Loop pro Testfunktion) — wie task_service._start_lock.
_account_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, asyncio.Lock]]" = (
    weakref.WeakKeyDictionary()
)


def account_fetch_lock(account_id: int) -> asyncio.Lock:
    """Lock pro Konto: verhindert parallelen Scheduler- und manuellen Abruf."""
    loop = asyncio.get_running_loop()
    locks = _account_locks.get(loop)
    if locks is None:
        locks = _account_locks[loop] = {}
    lock = locks.get(account_id)
    if lock is None:
        lock = locks[account_id] = asyncio.Lock()
    return lock


# ---------------------------------------------------------------------------
# Verarbeitung
# ---------------------------------------------------------------------------


def _empty_stats() -> dict:
    return {
        "total": 0, "relevant": 0, "irrelevant": 0, "skipped": 0, "failed": 0,
        "remaining": 0, "error": None,
    }


async def _record_account_status(
    session_factory: SessionFactory, account_id: int, last_error: str | None
) -> None:
    async with session_factory() as session:
        account = await session.get(EmailAccount, account_id)
        if account is None:
            return
        account.last_checked_at = datetime.now(timezone.utc)
        account.last_error = last_error
        await session.commit()


def _unlink_quietly(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Upload-Datei %s konnte nicht entfernt werden", path)


async def _create_jobs_from_email(
    parsed: dict,
    account_id: int,
    session: AsyncSession,
    settings: Settings,
    written_paths: list[Path],
) -> tuple[list[str], list[str]]:
    """Erstellt ProcessingJobs fuer Anhaenge und ggf. E-Mail-Body.

    Returns: (job_ids, skip_reasons). Alle geschriebenen Dateien landen in
    ``written_paths`` (Aufraeumen bei Rollback durch den Aufrufer).
    """
    job_ids: list[str] = []
    skip_reasons: list[str] = []
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    for att in parsed["attachments"]:
        filename = att["filename"]
        content = att["content"]
        # Wie beim HTTP-Upload: Typ und Groesse VOR dem Schreiben pruefen, damit
        # unzulaessige oder zu grosse Anhaenge gar nicht erst auf die Platte kommen.
        try:
            validate_file_metadata(filename, len(content), settings)
        except FileValidationError as e:
            logger.info("Anhang %s uebersprungen: %s", filename, e.message)
            skip_reasons.append(f"{filename}: {e.message}")
            continue

        stored_name = generate_stored_filename(filename)
        dest_path = upload_dir / stored_name
        await asyncio.to_thread(dest_path.write_bytes, content)
        written_paths.append(dest_path)
        try:
            # Gleiche Validierung wie beim manuellen Upload (inkl. Magic-Bytes).
            validate_file(dest_path, filename, len(content), settings)
        except FileValidationError as e:
            logger.warning("Anhang %s abgelehnt: %s", filename, e.message)
            skip_reasons.append(f"{filename}: {e.message}")
            written_paths.remove(dest_path)
            _unlink_quietly([dest_path])
            continue

        job = await create_job(
            dest_path, stored_name, filename, len(content), JobSource.EMAIL, session,
            email_account_id=account_id,
        )
        job_ids.append(job.id)
        logger.info("E-Mail-Anhang als Job erstellt: %s -> %s", filename, job.id)

    # F-07: Body als .txt-File, aber ocr_text vorab am Job gesetzt. Damit
    # wird OCR-Tesseract uebersprungen (Body ist bereits Text), und der
    # file_type="txt"-Pfad geht durch keine ALLOWED_FILE_TYPES-Schwelle die
    # ausserhalb der intern erzeugten E-Mail-Quelle relevant waere.
    body = (parsed.get("body") or "").strip()
    if not job_ids:
        if len(body) > _MIN_BODY_DOCUMENT_CHARS:
            # M-03 (Re-Review): Windows-Reserved-Characters (<>:"|?*) + Control-Chars
            # entschaerfen, sonst legt OneDrive-Sync/SMB-Share das File nicht an.
            safe_subject = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (parsed.get("subject") or "")[:50])
            txt_filename = f"email_{safe_subject}.txt"
            stored_name = generate_stored_filename(txt_filename)
            dest_path = upload_dir / stored_name
            await asyncio.to_thread(dest_path.write_text, body, encoding="utf-8")
            written_paths.append(dest_path)

            job = ProcessingJob(
                original_filename=txt_filename,
                stored_filename=stored_name,
                file_path=str(dest_path),
                file_type="txt",
                file_size_bytes=len(body.encode("utf-8")),
                source=JobSource.EMAIL,
                status=JobStatus.PENDING,
                email_account_id=account_id,
                # F-07: Body ist bereits sauberer Text — OCR ueberspringen,
                # Konfidenz 1.0 (keine Erkennungs-Unsicherheit).
                ocr_text=body,
                ocr_confidence=1.0,
            )
            session.add(job)
            await session.flush()
            job_ids.append(job.id)
            logger.info("E-Mail-Body als Job erstellt: %s -> %s", txt_filename, job.id)
        elif not parsed["attachments"]:
            skip_reasons.append("kein Anhang und zu wenig Text für ein eigenes Dokument")

    return job_ids, skip_reasons


async def _store_failed_record(
    session_factory: SessionFactory,
    account_id: int,
    message_id: str,
    parsed: dict | None,
    reason: str,
) -> None:
    """T16: FAILED-Eintrag, damit die Mail nicht bei jedem Lauf neu LLM-Calls erzeugt."""
    try:
        async with session_factory() as session:
            session.add(ProcessedEmail(
                email_account_id=account_id,
                message_id=message_id,
                subject=(parsed or {}).get("subject"),
                sender=(parsed or {}).get("sender"),
                received_at=(parsed or {}).get("date"),
                status=EmailStatus.FAILED,
                relevance_reason=reason[:_MAX_REASON_LEN],
            ))
            await session.commit()
    except IntegrityError:
        # Gleiche Mail (gleiche Surrogat-ID) ist bereits als FAILED erfasst.
        logger.info("FAILED-Eintrag fuer %s existiert bereits", message_id)
    except Exception:
        logger.exception("Konnte FAILED-Eintrag nicht speichern")


async def _process_email(
    raw: bytes,
    account_id: int,
    session_factory: SessionFactory,
    settings: Settings,
) -> str:
    """Verarbeitet eine Mail in eigener kurzer Transaktion.

    Returns: "relevant" | "irrelevant" | "skipped" | "failed".
    """
    parsed: dict | None = None
    written_paths: list[Path] = []
    committed = False
    try:
        parsed = parse_email_message(raw)

        async with session_factory() as session:
            existing = await session.execute(
                select(ProcessedEmail.id).where(
                    ProcessedEmail.email_account_id == account_id,
                    ProcessedEmail.message_id == parsed["message_id"],
                )
            )
            if existing.first() is not None:
                return "skipped"

        # LLM-Aufruf ausserhalb jeder DB-Transaktion. Kuerzen macht der
        # Relevanz-Service selbst (einmal, an einer Stelle).
        relevance = await check_email_relevance(
            sender=parsed["sender"],
            subject=parsed["subject"],
            body_snippet=parsed["body"],
            attachment_names=[a["filename"] for a in parsed["attachments"]],
            settings=settings,
        )
        reason = (relevance.get("reason") or "")[:_MAX_REASON_LEN]

        async with session_factory() as session:
            outcome = "irrelevant"
            status = EmailStatus.IRRELEVANT
            job_ids: list[str] = []
            if relevance["relevant"]:
                job_ids, skip_reasons = await _create_jobs_from_email(
                    parsed, account_id, session, settings, written_paths
                )
                if job_ids:
                    outcome, status = "relevant", EmailStatus.RELEVANT
                else:
                    # Relevant, aber nichts Archivierbares -> nicht stillschweigend
                    # als RELEVANT verbuchen, sondern sichtbar als FAILED.
                    outcome, status = "failed", EmailStatus.FAILED
                    reason = (
                        "Als relevant erkannt, aber kein verwertbares Dokument — "
                        + "; ".join(skip_reasons or ["keine verwertbaren Anhänge"])
                    )[:_MAX_REASON_LEN]
                    notify_system(
                        session,
                        "E-Mail ohne verwertbares Dokument",
                        f"„{parsed['subject'] or '(kein Betreff)'}“ von "
                        f"{parsed['sender'] or 'unbekannt'}: {reason}",
                    )

            session.add(ProcessedEmail(
                email_account_id=account_id,
                message_id=parsed["message_id"],
                subject=parsed["subject"],
                sender=parsed["sender"],
                received_at=parsed["date"],
                status=status,
                relevance_reason=reason,
                processing_job_id=job_ids[0] if job_ids else None,
            ))
            await session.commit()  # T16: pro E-Mail committen (inkl. der Jobs)
            committed = True
        return outcome

    except Exception as e:
        # Session-Kontext ist hier bereits verlassen (= Rollback). Geschriebene
        # Upload-Dateien gehoeren zu keinem Job mehr -> entfernen.
        if not committed:
            _unlink_quietly(written_paths)
        err_msg = str(e)[:400] or type(e).__name__
        logger.exception("Fehler bei E-Mail (Konto %s): %s", account_id, err_msg)
        # R-06 (Re-Review): parsed wiederverwenden statt re-parsen. Wenn
        # parse_email_message selbst gecrasht ist, stabilen Surrogat nutzen.
        message_id = parsed["message_id"] if parsed else _surrogate_message_id(raw)
        await _store_failed_record(
            session_factory, account_id, message_id, parsed,
            f"Verarbeitung fehlgeschlagen: {err_msg}",
        )
        return "failed"
    except BaseException:
        # Abbruch (CancelledError beim Herunterfahren): wie upload_service keine
        # verwaisten Upload-Dateien zuruecklassen.
        if not committed:
            _unlink_quietly(written_paths)
        raise


async def fetch_emails_for_account(
    account_id: int,
    session_factory: SessionFactory,
    settings: Settings,
    progress: ProgressCallback | None = None,
) -> dict:
    """Ruft E-Mails fuer ein Konto ab und verarbeitet sie (non-blocking).

    Der Aufrufer muss ``account_fetch_lock(account_id)`` halten, wenn parallel
    ein anderer Abruf desselben Kontos moeglich ist.

    Returns: Statistik-Dict (``EmailFetchResult``); ``error`` ist bei einem
    Abbruch (Verbindung, Schluessel, Konto) gesetzt, sonst None.
    """
    stats = _empty_stats()

    async def _progress(percent: int | None, text: str | None) -> None:
        if progress is not None:
            await progress(percent, text)

    async with session_factory() as session:
        account = await session.get(EmailAccount, account_id)
        if account is None:
            stats["error"] = "E-Mail-Konto nicht gefunden."
            return stats
        snapshot = _AccountSnapshot.of(account)

    try:
        password = _decrypt_account_password(snapshot.encrypted_password, settings)
        await _progress(5, "Verbinde mit dem Mailserver …")
        batch = await asyncio.to_thread(
            _fetch_raw_emails_sync, snapshot, password, settings.IMAP_TIMEOUT,
            settings.EMAIL_MAX_PER_RUN,
        )
    except Exception as e:
        logger.error("E-Mail-Abruf fuer %s fehlgeschlagen: %r", snapshot.name, e)
        stats["error"] = classify_imap_error(e)
        await _record_account_status(session_factory, account_id, stats["error"])
        return stats

    stats["total"] = len(batch.messages)
    stats["remaining"] = batch.remaining
    if not batch.messages:
        logger.info("Keine neuen E-Mails fuer %s", snapshot.name)
        await _record_account_status(session_factory, account_id, None)
        return stats

    logger.info(
        "%d neue E-Mails fuer %s (%d weitere beim naechsten Abruf)",
        len(batch.messages), snapshot.name, batch.remaining,
    )

    processed_uids: list[bytes] = []
    count = len(batch.messages)
    for index, (uid, raw) in enumerate(batch.messages, start=1):
        await _progress(10 + int(80 * (index - 1) / count), f"E-Mail {index} von {count} wird geprüft …")
        outcome = await _process_email(raw, account_id, session_factory, settings)
        stats[outcome] += 1
        # R-06: auch FAILED/Duplikate ablegen — sonst bleiben sie UNSEEN und
        # erzeugen bei jedem Lauf erneut Arbeit.
        processed_uids.append(uid)

    await _progress(92, "Verarbeitete E-Mails werden abgelegt …")
    problems: list[str] = []
    if stats["failed"]:
        problems.append(f"{stats['failed']} von {stats['total']} E-Mails fehlgeschlagen")
    try:
        warning = await asyncio.to_thread(
            _file_emails_sync, snapshot, password, settings.IMAP_TIMEOUT,
            processed_uids, batch.uid_validity,
        )
        if warning:
            problems.append(warning)
    except Exception as e:
        logger.warning("E-Mails konnten nicht abgelegt werden fuer %s: %r", snapshot.name, e)
        problems.append(f"E-Mails konnten nicht abgelegt werden: {classify_imap_error(e)}")

    await _record_account_status(session_factory, account_id, "; ".join(problems) or None)
    return stats
