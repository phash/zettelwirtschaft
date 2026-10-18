r"""Zugangsdaten und Erreichbarkeits-Status der Watch-/Export-Ordner.

Die Zugangsdaten eines Netzwerkordners (``\\server\freigabe``) liegen im
Key-Value-Speicher ``SystemSetting``: ``<ziel>_dir_net_user`` im Klartext,
``<ziel>_dir_net_password`` als Fernet-Token (gleicher Schluessel wie die
E-Mail-Passwoerter, ``EMAIL_ENCRYPTION_KEY``). Das Klartext-Passwort existiert
nur kurz im Speicher (``NetworkCredential``) — nie in Logs, Status,
API-Antworten oder Backups (dort steht nur das Token, der Schluessel liegt in
config.toml/.env).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core import network_share
from app.core.network_share import NetworkCredential, ShareError
from app.services.crypto_service import decrypt_password, encrypt_password
from app.services.settings_service import get_db_setting

logger = logging.getLogger(__name__)

WATCH = "watch"
EXPORT = "export"
TARGETS = (WATCH, EXPORT)

DECRYPT_FAILED_MESSAGE = (
    "Netzwerkordner: Das gespeicherte Passwort kann nicht entschlüsselt werden "
    "(EMAIL_ENCRYPTION_KEY geändert?) – bitte Passwort neu eingeben."
)
MISSING_CREDENTIALS_MESSAGE = (
    "Netzwerkordner: Benutzer und Passwort der Freigabe fehlen – bitte in den Einstellungen eintragen."
)


def user_key(target: str) -> str:
    return f"{target}_dir_net_user"


def password_key(target: str) -> str:
    return f"{target}_dir_net_password"


class CredentialKeyMissing(Exception):
    """EMAIL_ENCRYPTION_KEY fehlt — Passwoerter koennen nicht gespeichert werden."""


class CredentialDecryptFailed(Exception):
    pass


def encrypt_secret(password: str, settings: Settings) -> str:
    if not settings.EMAIL_ENCRYPTION_KEY:
        raise CredentialKeyMissing()
    try:
        return encrypt_password(password, settings.EMAIL_ENCRYPTION_KEY)
    except ValueError:
        raise CredentialKeyMissing() from None


def decrypt_secret(token: str, settings: Settings) -> str:
    if not settings.EMAIL_ENCRYPTION_KEY:
        raise CredentialKeyMissing()
    try:
        return decrypt_password(token, settings.EMAIL_ENCRYPTION_KEY)
    except (InvalidToken, ValueError):
        raise CredentialDecryptFailed() from None


@dataclass(frozen=True)
class FolderConfig:
    path: str
    user: str
    token: str  # Fernet-Token, "" = kein Passwort gespeichert

    @property
    def network(self) -> bool:
        return network_share.is_unc(self.path)


async def load_folder_config(session: AsyncSession, target: str, settings: Settings) -> FolderConfig:
    default = settings.WATCH_DIR if target == WATCH else settings.EXPORT_DIR
    path = await get_db_setting(session, f"{target}_dir", default)
    if target == WATCH and not path:
        path = settings.WATCH_DIR
    return FolderConfig(
        path=path,
        user=await get_db_setting(session, user_key(target), ""),
        token=await get_db_setting(session, password_key(target), ""),
    )


def credential_for(config: FolderConfig, settings: Settings) -> NetworkCredential:
    """Zugangsdaten eines Netzwerkordners — wirft ShareError mit deutscher Meldung."""
    if not config.user or not config.token:
        raise ShareError(network_share.ERROR_LOGON_FAILURE, message=MISSING_CREDENTIALS_MESSAGE)
    try:
        return NetworkCredential(config.user, decrypt_secret(config.token, settings))
    except CredentialKeyMissing:
        from app.services.email_fetch_service import ENCRYPTION_KEY_MISSING_MESSAGE

        raise ShareError(network_share.ERROR_LOGON_FAILURE, message=f"Netzwerkordner: {ENCRYPTION_KEY_MISSING_MESSAGE}") from None
    except CredentialDecryptFailed:
        raise ShareError(network_share.ERROR_LOGON_FAILURE, message=DECRYPT_FAILED_MESSAGE) from None


# --- Status fuer die Health-Anzeige ------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FolderStatus:
    """Thread-sicherer Status je Ziel; nach aussen nur Kopien, nie Zugangsdaten."""

    _FIELDS = ("mode", "state", "path", "user", "reason", "last_ok_at", "next_retry_at", "poll_interval", "checked_at")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}

    def update(self, target: str, **fields) -> None:
        unknown = set(fields) - set(self._FIELDS)
        if unknown:  # Schutz: nichts anderes (z.B. ein Passwort) landet im Status
            raise ValueError(f"Unbekannte Statusfelder: {sorted(unknown)}")
        with self._lock:
            current = self._data.setdefault(target, dict.fromkeys(self._FIELDS))
            current.update(fields)
            if fields.get("state") is not None:
                current["checked_at"] = _now_iso()
            if fields.get("state") == "ok":
                current["last_ok_at"] = _now_iso()
                current["reason"] = None
                current["next_retry_at"] = None

    def get(self, target: str) -> dict | None:
        with self._lock:
            data = self._data.get(target)
            return dict(data) if data else None

    def clear(self, target: str | None = None) -> None:
        with self._lock:
            if target is None:
                self._data.clear()
            else:
                self._data.pop(target, None)


status = FolderStatus()


def health_component(target: str) -> dict | None:
    """Health-Eintrag ``watch_folder``/``export_folder`` (None = kein Status).

    R11: Ein neuer Verbindungsversuch NACH einem Fehler (``verbinde`` mit
    noch gesetztem Grund) bleibt eine Warnung — sonst verschwaenden Grund und
    "Naechster Versuch" bei jedem Versuch und der Gesamtstatus flackerte.
    ``checked_at`` sagt, wann der Zustand zuletzt festgestellt wurde (der
    Export wird nur beim Archivieren geprueft).
    """
    data = status.get(target)
    if not data or not data.get("state"):
        return None
    state = data["state"]
    reason = data.get("reason")
    healthy = state == "ok" or (state == "verbinde" and not reason)
    if state == "ok":
        message = "erreichbar"
    elif state == "verbinde" and not reason:
        message = "verbinde …"
    elif state == "verbinde":
        message = f"nicht erreichbar (neuer Versuch läuft): {reason}"
    else:
        message = "nicht erreichbar" + (f": {reason}" if reason else "")
    return {
        "status": "ok" if healthy else "warning",
        "message": message,
        "mode": data.get("mode"),
        "state": data["state"],
        "path": data.get("path"),
        "user": data.get("user") or None,
        "reason": data.get("reason"),
        "last_ok_at": data.get("last_ok_at"),
        "next_retry_at": data.get("next_retry_at"),
        "poll_interval": data.get("poll_interval"),
        "checked_at": data.get("checked_at"),
    }


# --- Export auf eine Freigabe ------------------------------------------------------

# R2: nach einem Verbindungsfehler wird der Export eine Weile uebersprungen —
# sonst zahlte jedes Dokument erneut den vollen SMB-Timeout (20 s und mehr).
EXPORT_BACKOFF_START = 60.0
EXPORT_BACKOFF_MAX = 900.0
_export_backoff_lock = threading.Lock()
_export_backoff: dict = {"key": None, "until": 0.0, "delay": 0.0}


def _reset_export_backoff() -> None:
    with _export_backoff_lock:
        _export_backoff.update(key=None, until=0.0, delay=0.0)


def uses_own_connection(config: FolderConfig) -> bool:
    """Netzwerkordner mit eigenen Zugangsdaten (sonst: lokal bzw. Experten-Override)."""
    return config.network and bool(config.user or config.token)


def prepare_export(config: FolderConfig, settings: Settings) -> bool:
    """Stellt vor einer Export-Kopie die Verbindung sicher (blockierend).

    False = Export ueberspringen (Grund im Log und im Status). Ohne eigene
    Verbindung wird NIE auf den UNC-Pfad zugegriffen — Windows meldete sonst
    das Computerkonto am Server an (FH-01). Ausnahme: der Experten-Override
    ALLOW_NETWORK_FOLDERS ohne Zugangsdaten (bisheriges Verhalten).

    R10: immer ``acquire`` mit den gerade geladenen Zugangsdaten — eine noch
    gehaltene Verbindung mit alten Zugangsdaten wird dabei ersetzt.
    """
    if not config.network:
        return True
    if not uses_own_connection(config) and settings.ALLOW_NETWORK_FOLDERS:
        return True
    key = network_share.share_key(config.path)
    now = time.monotonic()
    with _export_backoff_lock:
        if _export_backoff["key"] == key and now < _export_backoff["until"]:
            return False  # Grund + naechster Versuch stehen schon im Status
    try:
        root = network_share.parse_unc(config.path).root
        cred = credential_for(config, settings)
        network_share.connections.acquire(EXPORT, root, cred)
    except (ShareError, network_share.UncPathError) as exc:
        with _export_backoff_lock:
            delay = EXPORT_BACKOFF_START if _export_backoff["key"] != key else min(
                max(_export_backoff["delay"] * 2, EXPORT_BACKOFF_START), EXPORT_BACKOFF_MAX,
            )
            _export_backoff.update(key=key, until=now + delay, delay=delay)
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        logger.warning("Export-Kopie übersprungen (%s): %s — neuer Versuch frühestens in %d s", config.path, exc, delay)
        status.update(
            EXPORT, mode="netzwerk", state="getrennt", path=config.path, user=config.user, reason=str(exc),
            next_retry_at=retry_at,
        )
        return False
    _reset_export_backoff()
    status.update(EXPORT, mode="netzwerk", state="ok", path=config.path, user=config.user)
    return True


def release_export() -> None:
    """Export-Verbindung freigeben (Einstellungen geaendert). Laufende
    Export-Kopien behalten ihre Verbindung bis zum Ende (Lease)."""
    network_share.connections.release(EXPORT)
    _reset_export_backoff()
    status.clear(EXPORT)
