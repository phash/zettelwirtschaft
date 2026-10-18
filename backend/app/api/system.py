"""System-Health und Backup-API."""

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core import network_share
from app.core.install_dir import native_install_dir
from app.core.network_share import NetworkCredential, ShareError, UncPathError
from app.core.rate_limit import check_network_folder_rate
from app.services import network_folder_service as nfs
from app.core.version import read_app_version
from app.database import SQLITE_BUSY_TIMEOUT_MS, get_db, get_session_factory
from app.models.document import Document, DocumentStatus
from app.schemas.background_task import TaskResponse
from app.services.backup_service import (
    BackupSpaceError,
    backup_dir,
    cleanup_old_backups,
    create_backup,
    get_system_info,
    list_backups,
)
from app.services.ollama_client import OLLAMA_MIN_VERSION, get_last_outcomes, ollama_health
from app.services.settings_service import get_db_setting, set_db_setting
from app.services.task_service import TaskContext, TaskError, start_task

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


_NET_USER_MAX = 256
_NET_PASSWORD_MAX = 256


class ScannerShareStatus(BaseModel):
    """(B) Lokale Scanner-Freigabe des Eingangsordners — nie ein Passwort."""

    configured: bool = False
    exists: bool = False
    unc_path: str = ""
    user: str = ""
    path: str = ""
    matches_watch_dir: bool = False


class FolderSettings(BaseModel):
    watch_dir: str
    export_dir: str
    watch_dir_host: str = ""
    export_dir_host: str = ""
    restart_required: bool = False
    # Zugangsdaten fuer Netzwerkordner (\\server\freigabe) — nur Native/Dev.
    # Passwoerter sind write-only: None = unveraendert, "" = loeschen.
    watch_dir_net_user: str = Field("", max_length=_NET_USER_MAX)
    # Laenge wird im Handler geprueft (eine 422-Antwort wuerde den Wert zurueckspiegeln).
    watch_dir_net_password: str | None = None
    export_dir_net_user: str = Field("", max_length=_NET_USER_MAX)
    export_dir_net_password: str | None = None
    # Pruefintervall fuer Netzwerk-Watch-Ordner (Sekunden); None = unveraendert.
    watch_poll_interval: int | None = Field(None, ge=10, le=600)


class FolderSettingsResponse(FolderSettings):
    # "docker" | "native" | "dev" — das Frontend zeigt danach entweder die
    # Host-Mount-Felder (Docker) oder die Ordnerpfade direkt (Native/Dev).
    deployment_mode: str
    # Passwoerter werden NIE ausgeliefert (exclude), nur ob eines gespeichert ist.
    watch_dir_net_password: str | None = Field(None, exclude=True)
    export_dir_net_password: str | None = Field(None, exclude=True)
    watch_dir_net_password_set: bool = False
    export_dir_net_password_set: bool = False
    watch_poll_interval: int = 30
    network_folders_supported: bool = False
    scanner_share: ScannerShareStatus | None = None


class TestFolderRequest(BaseModel):
    target: Literal["watch", "export"]
    path: str = Field(max_length=1024)
    net_user: str = Field("", max_length=_NET_USER_MAX)
    net_password: str | None = None


class TestFolderResponse(BaseModel):
    ok: bool
    # local | unc | network_drive | subst | missing_drive | unsuitable
    kind: str
    message: str
    suggested_paths: list[str] = []


# Liest generate-mounts.ps1 (start.bat) im Docker-Betrieb und erzeugt daraus
# die Bind-Mounts in docker-compose.override.yml. Liegt im Datenordner
# (Container: /app/data = Host: <install>/data).
HOST_MOUNTS_FILENAME = ".host-mounts.json"
# Container-Pfade, unter denen die Host-Mounts im Docker-Betrieb erscheinen.
DOCKER_EXTERNAL_WATCH = "/app/external/watch"
DOCKER_EXTERNAL_EXPORT = "/app/external/export"


def host_mounts_file(settings: Settings) -> Path:
    return settings.data_dir / HOST_MOUNTS_FILENAME


def _write_host_mounts(settings: Settings, watch_host: str, export_host: str) -> None:
    """Schreibt die Host-Mount-Konfiguration nach <data>/.host-mounts.json."""
    mounts: dict = {}
    if watch_host:
        mounts["watch"] = watch_host
    if export_host:
        mounts["export"] = export_host
    path = host_mounts_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mounts:
        path.write_text(json.dumps(mounts, indent=2), encoding="utf-8")
    elif path.exists():
        path.unlink()


_FORBIDDEN_PATH_PREFIXES = (
    # Windows (case-insensitive Vergleich; lower() weiter unten). Fester
    # Fallback fuer Docker-Host-Pfade — lokal kommen die echten Orte aus den
    # Umgebungsvariablen dazu (_forbidden_prefixes).
    "c:\\windows", "c:/windows",
    "c:\\program files", "c:/program files",
    # Eigener Eintrag: "c:/program files (x86)" beginnt NICHT mit "c:/program files/".
    "c:\\program files (x86)", "c:/program files (x86)",
    "c:\\programdata", "c:/programdata",
    "c:\\$recycle.bin", "c:\\system volume information",
    # Linux/macOS System-Verzeichnisse
    "/etc", "/proc", "/sys", "/root", "/boot", "/dev",
    "/var/log", "/var/run", "/var/lib/docker",
    "/usr/bin", "/usr/sbin", "/sbin", "/bin",
)

# Windows-Systemorte, falls Windows nicht auf C: liegt bzw. umgeleitet ist.
_SYSTEM_DIR_ENV_VARS = (
    "SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
)

# FH-01: Win32-Geraete-/Langpfad-Praefixe. Python behaelt "\\?\" in resolve()
# bei — damit liefen frueher alle String-Pruefungen ins Leere
# ("\\?\C:\Windows\System32" wurde akzeptiert).
_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "//?/", "//./")


def _forbidden_prefixes() -> list[str]:
    prefixes = list(_FORBIDDEN_PATH_PREFIXES)
    if os.name == "nt":
        prefixes += [v for v in (os.environ.get(name) for name in _SYSTEM_DIR_ENV_VARS) if v]
    return prefixes


def _strip_device_prefix(path_str: str) -> str:
    r"""``\\?\C:\x`` -> ``C:\x``, ``\\?\UNC\srv\share`` -> ``\\srv\share``."""
    head = path_str[:8].replace("/", "\\").upper()
    if head == "\\\\?\\UNC\\":
        return "\\\\" + path_str[8:]
    if path_str.startswith(_DEVICE_PREFIXES):
        return path_str[4:]
    return path_str


def _is_network_path(path_str: str) -> bool:
    """UNC-Pfad (``\\server\freigabe``) — nach Entfernen eines Geraete-Praefixes."""
    return _strip_device_prefix(path_str).startswith(("\\\\", "//"))


def _reject_local_or_admin_share(path_str: str, label: str) -> None:
    r"""INFRA-02: Auch mit ALLOW_NETWORK_FOLDERS keine Freigabe auf DIESEM
    Rechner (``\\localhost\C$\...``, ``\\127.0.0.1\...``, ``\\<PC-Name>\...``,
    DNS-Alias auf eine eigene Adresse) und keine administrativen Freigaben
    (``C$``, ``ADMIN$``): darueber erreicht der LocalSystem-Dienst jeden lokalen
    Pfad an der Systemordner-, Profil- und Programmordner-Pruefung vorbei.
    Namens-/DNS-Pruefung, keine SMB-Anmeldung.
    """
    parts = [p for p in _strip_device_prefix(path_str).replace("/", "\\").split("\\") if p]
    host = parts[0].lower().strip("[]") if parts else ""
    share = parts[1].lower() if len(parts) > 1 else ""
    try:
        if network_share.is_admin_share(share):
            raise UncPathError("administrative Freigabe")
        network_share.check_host_not_local(host)
    except network_share.HostNotFound as exc:
        # Fail closed (NET-SEC-08): nicht pruefbar -> nicht verwenden.
        raise HTTPException(400, f"{label}: {network_share.MESSAGE_PREFIX}{exc}") from None
    except UncPathError:
        raise HTTPException(
            400,
            f"{label}: Freigaben auf diesem Rechner und administrative Freigaben (C$, ADMIN$) "
            f"sind nicht erlaubt — bitte den lokalen Pfad angeben: {path_str}",
        ) from None


def _canonical(path: Path | str, *, allow_unc: bool = False) -> Path:
    r"""Vergleichsform: Links/Junctions/8.3-Namen aufgeloest, ohne ``\\?\``,
    unter Windows klein geschrieben (NTFS ist case-insensitive).

    NET-SEC-01: ``realpath`` oeffnet den Pfad (CreateFileW). Auf einem
    Netzwerkpfad meldete sich der Dienst ohne eigene Verbindung mit dem
    Computerkonto an — deshalb nur mit ``allow_unc`` (Experten-Override),
    sonst ``ValueError`` statt Netzwerkzugriff.
    """
    if _is_network_path(str(path)) and not allow_unc:
        raise ValueError("Netzwerkpfad ohne Verbindung wird nicht aufgelöst")
    return Path(os.path.normcase(_strip_device_prefix(os.path.realpath(str(path)))))


def _canonical_or_lexical(path: Path | str) -> Path:
    """Wie ``_canonical``, Netzwerkpfade aber nur textuell (kein Zugriff)."""
    if _is_network_path(str(path)):
        return Path(os.path.normcase(_strip_device_prefix(str(path))))
    return _canonical(path)


def _normalize_path_input(value: str | None) -> str:
    """Leerraum und umschliessende Anfuehrungszeichen entfernen.

    Windows-Explorer "Als Pfad kopieren" liefert "C:\\..." in Anfuehrungszeichen;
    Anfuehrungszeichen sind in Windows-Pfaden ohnehin unzulaessig.
    """
    return (value or "").strip().strip("\"'").strip()


def _reject_device_prefix(path_str: str, label: str) -> None:
    if path_str.startswith(_DEVICE_PREFIXES):
        raise HTTPException(
            400, f"{label}: Pfade mit \\\\?\\ oder \\\\.\\ sind nicht erlaubt — bitte normal angeben (z.B. D:\\Scans): {path_str}",
        )


def _reject_dangerous(path_str: str, label: str) -> None:
    """Systemverzeichnisse und '..'-Segmente ablehnen (docs/reviews/SECURITY_AUDIT_v2.md N-003)."""
    if ".." in path_str.replace("\\", "/").split("/"):
        raise HTTPException(400, f"{label} enthält unerlaubte Pfad-Segmente: {path_str}")
    p = _strip_device_prefix(path_str).lower().replace("\\", "/").rstrip("/")
    for forbidden in _forbidden_prefixes():
        f = forbidden.lower().replace("\\", "/").rstrip("/")
        if p == f or p.startswith(f + "/"):
            raise HTTPException(400, f"{label} darf nicht auf Systemverzeichnisse zeigen: {path_str}")


def _validate_host_path(value: str, label: str) -> str:
    """Docker: Host-Pfad fuer einen Bind-Mount pruefen; liefert ihn normalisiert.

    Der Host kann Windows oder Linux sein — daher beide Pfadformen. Ohne die
    Pruefung koennte jemand `C:\\Windows\\System32` in den Container mounten
    (N-003).
    """
    path_str = _normalize_path_input(value)
    if not path_str:
        return ""  # leer = deaktiviert
    _reject_device_prefix(path_str, label)
    _reject_dangerous(path_str, label)
    win = PureWindowsPath(path_str)
    if not (win.is_absolute() or path_str.startswith("/")):
        raise HTTPException(400, f"{label} muss ein absoluter Pfad sein: {path_str}")
    if (win.is_absolute() and str(win) == win.anchor) or path_str.rstrip("/") == "":
        raise HTTPException(400, f"{label} darf kein Laufwerks- oder Wurzelverzeichnis sein: {path_str}")
    return path_str


def _users_root() -> Path | None:
    """``C:\\Users`` (bzw. umgeleitet) — nur unter Windows."""
    if os.name != "nt":
        return None
    public = os.environ.get("PUBLIC")
    if public:
        return Path(public).parent
    drive = os.environ.get("SystemDrive")
    return Path(drive + "\\Users") if drive else None


def _profile_name(canonical_path: Path, canonical_root: Path) -> str | None:
    """Erster Ordner unterhalb von ``C:\\Users`` ("" fuer den Ordner selbst),
    None ausserhalb."""
    if not canonical_path.is_relative_to(canonical_root):
        return None
    parts = canonical_path.relative_to(canonical_root).parts
    return parts[0] if parts else ""


def _reject_foreign_profile(resolved: Path, settings: Settings, label: str, path_str: str) -> None:
    """FH-01: Der Native-Dienst laeuft als LocalSystem und kann jedes
    Benutzerprofil lesen. Erlaubt sind nur das Profil, in dem der Datenordner
    liegt (der installierende Benutzer), das eigene Profil im Dev-Betrieb und
    ``C:\\Users\\Public`` — sonst liesse sich ueber den Watch-Ordner jede
    Datei eines anderen Benutzers importieren (und dort wegverschieben).
    """
    root = _users_root()
    if root is None:
        return
    canonical_root = _canonical(root)
    name = _profile_name(resolved, canonical_root)
    if name is None:
        return
    allowed = {os.path.normcase("Public")}
    for owner in (settings.data_dir, os.environ.get("USERPROFILE")):
        if owner:
            owner_name = _profile_name(_canonical(owner), canonical_root)
            if owner_name:
                allowed.add(owner_name)
    if name not in allowed:
        raise HTTPException(
            400,
            f"{label} darf nicht im Profil eines anderen Benutzers liegen — erlaubt sind "
            f"das eigene Profil, „{root / 'Public'}“ oder Ordner außerhalb von „{root}“: {path_str}",
        )


def _validate_local_dir(value: str, label: str, settings: Settings) -> str:
    r"""Pfad, den das Backend selbst liest/schreibt (Watch-/Export-Ordner).

    Muss absolut sein, darf weder System- noch Wurzelverzeichnis sein und
    nicht in die von der App verwalteten Ordner (Daten-, Archiv-, Upload-,
    Thumbnail-, Backup- und Programmordner) zeigen — der Watcher wuerde sonst
    die eigenen Ablagen einlesen bzw. der Export ins Archiv schreiben.

    FH-01: Verglichen wird die kanonische Form (Junctions/8.3-Namen
    aufgeloest, ohne ``\\?\``, case-insensitive). Netzwerkpfade nur mit
    ALLOW_NETWORK_FOLDERS — und dann *vor* jedem Dateisystemzugriff geprueft,
    weil schon ``resolve()`` den Server kontaktiert (NTLM des Dienstkontos).
    """
    path_str = _normalize_path_input(value)
    _reject_device_prefix(path_str, label)
    network = _is_network_path(path_str)
    if network and not settings.ALLOW_NETWORK_FOLDERS:
        if settings.deployment_mode == "docker":
            hint = "im Docker-Betrieb bitte als CIFS-Volume einbinden (siehe Hilfe)"
        else:
            hint = "brauchen Benutzer und Passwort der Freigabe – bitte beide angeben"
        raise HTTPException(400, f"{label}: Netzwerkpfade (\\\\server\\freigabe) {hint}: {path_str}")
    if network:
        _reject_local_or_admin_share(path_str, label)
    else:
        path_str = _resolve_drive_letter(path_str, label)
    _reject_dangerous(path_str, label)
    path = Path(path_str)
    if not path.is_absolute():
        raise HTTPException(400, f"{label} muss ein absoluter Pfad sein: {path_str}")
    if not network:
        # Vor realpath: eine Verknuepfung auf eine Freigabe wuerde sonst schon
        # beim Aufloesen geoeffnet (Computerkonto, NET-SEC-07).
        link = network_share.find_remote_link(str(path))
        if link and not settings.ALLOW_NETWORK_FOLDERS:
            raise HTTPException(400, f"{label}: {network_share.remote_link_message(link)}")
    resolved = _canonical(path, allow_unc=network or settings.ALLOW_NETWORK_FOLDERS)
    if _is_network_path(str(resolved)) and not settings.ALLOW_NETWORK_FOLDERS:
        # Lokaler Pfad, der per Link auf eine Freigabe zeigt.
        raise HTTPException(400, f"{label} zeigt auf einen Netzwerkpfad: {path_str}")
    _reject_dangerous(str(resolved), label)
    if resolved.parent == resolved and not network:
        raise HTTPException(400, f"{label} darf kein Laufwerks- oder Wurzelverzeichnis sein: {path_str}")

    data_dir = _canonical_or_lexical(settings.data_dir)
    if resolved == data_dir or data_dir.is_relative_to(resolved):
        raise HTTPException(400, f"{label} darf nicht der Datenordner oder einer seiner Elternordner sein: {path_str}")
    managed = [settings.ARCHIVE_DIR, settings.UPLOAD_DIR, settings.THUMBNAIL_DIR, str(settings.data_dir / "backups")]
    install_dir = native_install_dir()
    if install_dir is not None:
        managed.append(str(install_dir))
    for managed_dir in managed:
        if resolved.is_relative_to(_canonical_or_lexical(managed_dir)):
            raise HTTPException(400, f"{label} darf nicht in einem Programm- oder Ablageordner liegen: {path_str}")
    _reject_foreign_profile(resolved, settings, label, path_str)
    return str(path)


def _resolve_drive_letter(path_str: str, label: str) -> str:
    r"""Laufwerksbuchstaben pruefen, bevor irgendetwas aufgeloest wird.

    Der Dienst (LocalSystem) sieht weder die Netzlaufwerke noch die
    subst-Laufwerke der Benutzersitzung. Netzlaufwerke werden abgelehnt (mit
    UNC-Vorschlag, falls aufloesbar), subst-Laufwerke auf das echte Ziel
    umgeschrieben (gespeichert wird das Ziel). Lokale feste und wechselbare
    Laufwerke bleiben unveraendert erlaubt.
    """
    info = network_share.classify_drive(path_str)
    if info.kind in ("none", "local"):
        return path_str
    if info.kind == "subst" and info.target:
        return info.target
    raise HTTPException(400, f"{label}: {network_share.drive_message(info)}")


def _network_supported(settings: Settings) -> bool:
    """Netzwerkordner mit eigenen Zugangsdaten: nur Native/Dev unter Windows."""
    return settings.deployment_mode != "docker" and network_share.supported()


def _validate_network_dir(path_str: str, label: str) -> str:
    r"""UNC-Pfad fuer den Zugangsdaten-Ablauf pruefen — OHNE Dateisystemzugriff.

    Form ``\\host\share[\sub...]``, keine Geraete-Praefixe, WebDAV-Formen,
    IPv6/Ports, administrativen Freigaben und keine Freigaben dieses Rechners
    (auch nicht per DNS-Alias). Liefert die normalisierte Form.
    """
    try:
        unc = network_share.parse_unc(path_str)
    except UncPathError as exc:
        raise HTTPException(400, f"{label}: {exc}") from None
    _reject_local_or_admin_share(unc.path, label)
    _reject_dangerous(unc.path, label)
    return unc.path


@dataclass
class _FolderPlan:
    """Ergebnis der Validierung eines Ordners (noch nichts gespeichert)."""

    target: str
    label: str
    path: str
    user: str = ""
    token: str = ""  # Fernet-Token, das gespeichert wird
    credential: NetworkCredential | None = None  # nur im Speicher, nie ausgeliefert
    # Passwort aus dem Speicher wiederverwendet (nicht neu eingegeben) — wird
    # nie mit einem eingegebenen verglichen (SEC-NF-01).
    stored_password: bool = False

    @property
    def network(self) -> bool:
        return network_share.is_unc(self.path)

    @property
    def uses_credentials(self) -> bool:
        return self.network and self.credential is not None


_RETYPE_PASSWORD = (
    "Server, Freigabe oder Benutzer wurden geändert – bitte das Passwort der Freigabe erneut eingeben."
)


def _encryption_unavailable() -> HTTPException:
    from app.services.email_fetch_service import ENCRYPTION_KEY_MISSING_MESSAGE

    return HTTPException(503, ENCRYPTION_KEY_MISSING_MESSAGE)


def _resolve_credential(
    path_str: str, user_in: str, password_in: str | None, stored: nfs.FolderConfig, settings: Settings, label: str,
) -> tuple[str, str, NetworkCredential | None, bool]:
    r"""(Benutzer, Token, Zugangsdaten, aus dem Speicher?) fuer einen UNC-Pfad.

    SEC-DATA-02: Das gespeicherte Passwort wird nur wiederverwendet, wenn
    ``\\host\share`` UND Benutzer unveraendert sind — sonst liesse sich das
    NAS-Passwort per Pfadwechsel an einen beliebigen Server schicken.
    """
    user = (user_in or "").strip()
    if password_in is not None and len(password_in) > _NET_PASSWORD_MAX:
        raise HTTPException(400, f"{label}: Das Passwort ist zu lang (höchstens {_NET_PASSWORD_MAX} Zeichen).")
    if password_in and not user:
        return user, "", None, False
    if password_in:
        try:
            token = nfs.encrypt_secret(password_in, settings)
        except nfs.CredentialKeyMissing:
            raise _encryption_unavailable() from None
        return user, token, (NetworkCredential(user, password_in) if user else None), False
    if password_in == "" or not stored.token:
        return user, "", None, False
    same_share = network_share.share_key(path_str) is not None and (
        network_share.share_key(path_str) == network_share.share_key(stored.path)
    )
    if not (same_share and user.casefold() == stored.user.casefold() and user):
        if user:
            raise HTTPException(400, f"{label}: {_RETYPE_PASSWORD}")
        return user, "", None, False
    try:
        password = nfs.decrypt_secret(stored.token, settings)
    except nfs.CredentialKeyMissing:
        raise _encryption_unavailable() from None
    except nfs.CredentialDecryptFailed:
        raise HTTPException(400, f"{label}: {nfs.DECRYPT_FAILED_MESSAGE}") from None
    return user, stored.token, NetworkCredential(user, password), True


def _plan_folder(
    target: str, value: str, user_in: str, password_in: str | None,
    stored: nfs.FolderConfig, settings: Settings,
) -> _FolderPlan:
    label = "Watch-Ordner" if target == nfs.WATCH else "Export-Ordner"
    path_str = _normalize_path_input(value)
    default = settings.WATCH_DIR if target == nfs.WATCH else settings.EXPORT_DIR
    if not path_str or (path_str == default and not _is_network_path(path_str)):
        # Der konfigurierte lokale Standard braucht keine Pruefung. Ein UNC-
        # Standard aus config.toml dagegen schon (NET-SEC-06): ohne
        # Zugangsdaten wird er abgelehnt bzw. nie ohne Verbindung angefasst.
        if not path_str and target == nfs.WATCH and _is_network_path(settings.WATCH_DIR):
            path_str = settings.WATCH_DIR
        else:
            return _FolderPlan(target, label, settings.WATCH_DIR if target == nfs.WATCH else path_str)
    _reject_device_prefix(path_str, label)
    if not _is_network_path(path_str):
        return _FolderPlan(target, label, _validate_local_dir(path_str, label, settings))

    user, token, credential, from_storage = _resolve_credential(
        path_str, user_in, password_in, stored, settings, label,
    )
    if credential is None:
        if user or password_in:
            raise HTTPException(400, f"{label}: Bitte Benutzer und Passwort der Freigabe angeben.")
        # Ohne Zugangsdaten: nur mit Experten-Override (bisheriges Verhalten),
        # sonst die bekannte Ablehnung — beides vor jedem Dateisystemzugriff.
        return _FolderPlan(target, label, _validate_local_dir(path_str, label, settings))
    if not _network_supported(settings):
        raise HTTPException(400, f"{label}: {network_share.NetworkShareUnsupported()}")
    return _FolderPlan(
        target, label, _validate_network_dir(path_str, label), user=user, token=token, credential=credential,
        stored_password=from_storage,
    )


def _check_same_host(watch: _FolderPlan, export: _FolderPlan) -> None:
    """SMB: pro Server nur EIN Satz Zugangsdaten je Anmeldesitzung (Fehler 1219).

    SEC-NF-01: Ein gespeichertes Passwort wird NIE mit einem eingegebenen
    verglichen — die Antwort verriete sonst, ob die Eingabe dem gespeicherten
    NAS-Passwort entspricht (Raten ohne Anmeldeversuch an der NAS). Ist eine
    Seite neu eingegeben und die andere gespeichert, gelten die eingegebenen
    Zugangsdaten fuer beide; die Verbindungsprobe prueft sie dann an der NAS.
    """
    if not (watch.uses_credentials and export.uses_credentials):
        return
    host_w = network_share.parse_unc(watch.path).host.casefold()
    host_e = network_share.parse_unc(export.path).host.casefold()
    if host_w != host_e:
        return
    conflict = HTTPException(400, network_share.message_for(network_share.ERROR_SESSION_CREDENTIAL_CONFLICT))
    if watch.credential.user.casefold() != export.credential.user.casefold():
        raise conflict
    if watch.stored_password != export.stored_password:
        fresh, stored = (export, watch) if watch.stored_password else (watch, export)
        stored.credential, stored.token, stored.stored_password = fresh.credential, fresh.token, False
        return
    if not watch.credential.same_as(export.credential):
        raise conflict


def _probe_network_folder(path_str: str, credential: NetworkCredential, create: bool) -> str:
    r"""Verbinden, (anlegen,) Schreibtest — blockierend.

    Erst NACH dem Verbinden wird der Pfad angefasst, und zwar ueber die beim
    Verbinden gepruefte Adresse (``\\<ip>\share``, NET-SEC-08). DFS-Freigaben
    erkennt und verweigert schon ``connect`` — ohne der Weiterleitung zu folgen
    (NET-SEC-09). Die Verbindung wird erst nach dem letzten Zugriff dieses
    Threads freigegeben (auch wenn der wartende Request schon abgelaufen ist).
    Liefert eine Meldung fuer die UI.
    """
    unc = network_share.parse_unc(path_str)
    probe_target = f"probe:{uuid.uuid4().hex}"
    access_root = network_share.connections.acquire(probe_target, unc.root, credential)
    try:
        folder = _fs_path("\\".join([access_root, *unc.rest]))
        if not os.path.isdir(folder):
            if not create:
                return "Verbindung erfolgreich. Der Ordner existiert noch nicht und wird beim Speichern angelegt."
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                raise ShareError(0, message="Netzwerkordner: Verbindung erfolgreich, aber der Ordner kann nicht angelegt werden.") from None
        probe_file = os.path.join(folder, f".zw-test-{uuid.uuid4().hex}")
        try:
            with open(probe_file, "wb") as fh:
                fh.write(b"zettelwirtschaft")
        except OSError:
            raise ShareError(0, message="Netzwerkordner: Verbindung erfolgreich, aber der Benutzer darf in diesem Ordner nicht schreiben.") from None
        finally:
            try:
                os.remove(probe_file)
            except OSError:
                pass
        return "Verbindung erfolgreich – Ordner lesbar und beschreibbar."
    finally:
        network_share.connections.release(probe_target)


def _fs_path(unc_path: str) -> str:
    """Dateisystempfad zu einem UNC-Pfad — in Produktion identisch. Tests
    bilden Freigaben hierueber auf lokale Ordner ab (kein echtes SMB)."""
    return unc_path


# R8: Probe + Planung bleiben deutlich unter dem Zeitlimit der Oberflaeche.
_PROBE_TIMEOUT = 20.0
_VALIDATION_TIMEOUT = 15.0


async def _validate_in_thread(fn, *args):
    """Validierung im Thread (SEC-NF-02): Namensaufloesung (DNS/LLMNR/NetBIOS),
    realpath und Registry blockieren — nie den Event-Loop."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), _VALIDATION_TIMEOUT)
    except TimeoutError:
        raise HTTPException(
            400, "Zeitüberschreitung bei der Prüfung des Ordners (Namensauflösung) – bitte den Servernamen prüfen.",
        ) from None


async def _probe_or_400(plan: _FolderPlan, create: bool) -> str:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_probe_network_folder, plan.path, plan.credential, create), _PROBE_TIMEOUT,
        )
    except TimeoutError:
        raise HTTPException(400, f"{plan.label}: {network_share.message_for(network_share.ERROR_SEM_TIMEOUT)}") from None
    except (ShareError, UncPathError) as exc:
        raise HTTPException(400, f"{plan.label}: {exc}") from None
    except OSError as exc:
        code = getattr(exc, "winerror", None) or 0
        raise HTTPException(
            400, f"{plan.label}: {network_share.message_for(code) if code else 'Netzwerkordner: Zugriff fehlgeschlagen.'}",
        ) from None


def _effective_watch_dir(value: str, settings: Settings) -> str:
    """Leer oder der konfigurierte Standard -> app-verwalteter Standard-Watch-Ordner."""
    path_str = _normalize_path_input(value)
    if not path_str or path_str == settings.WATCH_DIR:
        return settings.WATCH_DIR
    return _validate_local_dir(path_str, "Watch-Ordner", settings)


def _effective_export_dir(value: str, settings: Settings) -> str:
    """Leer -> Export deaktiviert; der konfigurierte EXPORT_DIR bleibt zulaessig."""
    path_str = _normalize_path_input(value)
    if not path_str or path_str == settings.EXPORT_DIR:
        return path_str
    return _validate_local_dir(path_str, "Export-Ordner", settings)


SCANNER_SHARE_DEFAULT_NAME = "Zettelwirtschaft-Eingang"


def _scanner_share_status(watch_dir: str) -> ScannerShareStatus:
    """(B) Status der lokalen Scanner-Freigabe aus der Registry (vom Setup bzw.
    freigabe-einrichten.ps1 geschrieben), geprueft ueber NetShareGetInfo."""
    reg = network_share.scanner_share_registry()
    name = reg.get("ScannerShareName", "")
    exists, share_path = network_share.scanner_share_info(name or SCANNER_SHARE_DEFAULT_NAME)
    if not name and not exists:
        return ScannerShareStatus()
    name = name or SCANNER_SHARE_DEFAULT_NAME
    path = share_path or reg.get("ScannerSharePath", "")
    # Die Scanner-Freigabe ist immer ein LOKALER Ordner. Ein Netzwerk-Watch-
    # Ordner kann nicht derselbe sein — und wird nie ohne Verbindung
    # aufgeloest (realpath meldete das Computerkonto an, NET-SEC-01).
    if not path or network_share.is_unc(watch_dir) or network_share.is_unc(path):
        matches = False
    else:
        try:
            matches = _canonical(path) == _canonical(watch_dir)
        except (OSError, ValueError):
            matches = False
    computer = os.environ.get("COMPUTERNAME") or socket.gethostname()
    return ScannerShareStatus(
        configured=bool(reg.get("ScannerShareName")),
        exists=exists,
        unc_path=f"\\\\{computer}\\{name}",
        user=reg.get("ScannerShareUser", ""),
        path=path,
        matches_watch_dir=matches,
    )


async def _folder_settings_response(
    session: AsyncSession, settings: Settings, restart_required: bool = False,
) -> FolderSettingsResponse:
    docker = settings.deployment_mode == "docker"
    watch = await nfs.load_folder_config(session, nfs.WATCH, settings)
    export = await nfs.load_folder_config(session, nfs.EXPORT, settings)
    from app.services.watch_folder_service import POLL_INTERVAL_KEY, clamp_poll_interval

    interval = clamp_poll_interval(await get_db_setting(session, POLL_INTERVAL_KEY, "") or settings.WATCH_POLL_INTERVAL)
    supported = _network_supported(settings)
    scanner = None
    if supported:
        try:
            scanner = await asyncio.to_thread(_scanner_share_status, watch.path)
        except Exception:
            logger.warning("Status der Scanner-Freigabe nicht lesbar", exc_info=True)
    return FolderSettingsResponse(
        watch_dir=watch.path,
        export_dir=export.path,
        # Host-Mounts gibt es nur im Docker-Betrieb.
        watch_dir_host=await get_db_setting(session, "watch_dir_host", "") if docker else "",
        export_dir_host=await get_db_setting(session, "export_dir_host", "") if docker else "",
        restart_required=restart_required,
        deployment_mode=settings.deployment_mode,
        watch_dir_net_user="" if docker else watch.user,
        export_dir_net_user="" if docker else export.user,
        watch_dir_net_password_set=not docker and bool(watch.token),
        export_dir_net_password_set=not docker and bool(export.token),
        watch_poll_interval=interval,
        network_folders_supported=supported,
        scanner_share=scanner,
    )


@router.get("/system/settings", response_model=FolderSettingsResponse)
async def get_folder_settings(
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Liest die konfigurierten Ordner-Einstellungen (ohne Passwoerter)."""
    return await _folder_settings_response(session, settings)


@router.put("/system/settings", response_model=FolderSettingsResponse)
async def update_folder_settings(
    body: FolderSettings,
    request: Request,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Aktualisiert Watch-Ordner und Export-Ordner. Startet den Watch-Task neu,
    wenn sich Pfad, Zugangsdaten oder Pruefintervall aendern.

    Docker: die *_host-Felder sind Host-Ordner, die per Bind-Mount unter
    /app/external/* erscheinen (wirksam nach Neustart via start.bat).
    Native/Dev: das Backend greift direkt auf die Ordner zu — ein Host-Pfad
    (falls die UI ihn schickt) wird direkt als Ordnerpfad uebernommen.
    Netzwerkordner (\\\\server\\freigabe) brauchen Benutzer + Passwort; vor dem
    Speichern wird damit verbunden und ein Schreibtest gemacht.
    """
    mode = settings.deployment_mode
    from app.services.watch_folder_service import POLL_INTERVAL_KEY, clamp_poll_interval

    old_watch = await nfs.load_folder_config(session, nfs.WATCH, settings)
    old_export = await nfs.load_folder_config(session, nfs.EXPORT, settings)
    old_interval_raw = await get_db_setting(session, POLL_INTERVAL_KEY, "")
    old_interval = clamp_poll_interval(old_interval_raw or settings.WATCH_POLL_INTERVAL)
    interval = body.watch_poll_interval if body.watch_poll_interval is not None else old_interval
    watch_task_stopped = False

    # Erst ALLES validieren, dann speichern.
    if mode == "docker":
        watch_host = _validate_host_path(body.watch_dir_host, "Watch-Ordner")
        export_host = _validate_host_path(body.export_dir_host, "Export-Ordner")
        if watch_host:
            watch_dir = DOCKER_EXTERNAL_WATCH
        elif _normalize_path_input(body.watch_dir) == DOCKER_EXTERNAL_WATCH:
            watch_dir = settings.WATCH_DIR  # Host-Pfad geleert -> Standard
        else:
            watch_dir = _effective_watch_dir(body.watch_dir, settings)
        if export_host:
            export_dir = DOCKER_EXTERNAL_EXPORT
        elif _normalize_path_input(body.export_dir) == DOCKER_EXTERNAL_EXPORT:
            export_dir = ""  # Host-Pfad geleert -> Export aus
        else:
            export_dir = _effective_export_dir(body.export_dir, settings)
        old_watch_host = await get_db_setting(session, "watch_dir_host", "")
        old_export_host = await get_db_setting(session, "export_dir_host", "")
        restart_required = watch_host != old_watch_host or export_host != old_export_host
        # Docker: keine eigenen SMB-Verbindungen (CIFS-Volume) — Zugangsdaten bleiben leer.
        watch_plan = _FolderPlan(nfs.WATCH, "Watch-Ordner", watch_dir)
        export_plan = _FolderPlan(nfs.EXPORT, "Export-Ordner", export_dir)
    else:
        watch_host = export_host = ""
        watch_value = _normalize_path_input(body.watch_dir_host) or _normalize_path_input(body.watch_dir)
        export_value = _normalize_path_input(body.export_dir_host) or _normalize_path_input(body.export_dir)
        # Altlast: fruehere Versionen haben auch hier die Docker-Containerpfade
        # gespeichert — die zeigen ausserhalb von Docker ins Leere.
        if watch_value == DOCKER_EXTERNAL_WATCH:
            watch_value = ""
        if export_value == DOCKER_EXTERNAL_EXPORT:
            export_value = ""
        if any(_is_network_path(v) for v in (watch_value, export_value)) or (
            not watch_value and _is_network_path(settings.WATCH_DIR)
        ):
            # Eigenes, engeres Limit VOR jeder Pruefung (SEC-NF-01/02):
            # Verbindungsversuche, Namensaufloesung und Zugangsdaten-Vergleiche
            # taugen sonst zum Passwortraten bzw. zum Blockieren.
            check_network_folder_rate(request)

        def _plan_both() -> tuple[_FolderPlan, _FolderPlan]:
            w = _plan_folder(
                nfs.WATCH, watch_value, body.watch_dir_net_user, body.watch_dir_net_password, old_watch, settings,
            )
            e = _plan_folder(
                nfs.EXPORT, export_value, body.export_dir_net_user, body.export_dir_net_password, old_export, settings,
            )
            _check_same_host(w, e)
            return w, e

        watch_plan, export_plan = await _validate_in_thread(_plan_both)
        restart_required = False

        network_plans = [p for p in (watch_plan, export_plan) if p.uses_credentials]
        # Bestehende Verbindungen des jeweiligen Ziels vor dem Test freigeben
        # (sonst kollidiert ein Benutzer-/Passwortwechsel mit Fehler 1219).
        if watch_plan.uses_credentials and _watch_changed(old_watch, watch_plan):
            await _stop_watch_task(request.app)
            watch_task_stopped = True
        if export_plan.uses_credentials and _folder_changed(old_export, export_plan):
            await asyncio.to_thread(nfs.release_export)
        try:
            # Proben parallel (R8): die Gesamtdauer bleibt unter dem Zeitlimit der UI.
            results = await asyncio.gather(
                *(_probe_or_400(plan, create=True) for plan in network_plans), return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            # Lokale Ordner muessen existieren bzw. anlegbar sein — sonst wuerde
            # die UI Erfolg melden, waehrend der Watcher ins Leere schaut. (UNC
            # ohne Zugangsdaten nur per Experten-Override, wie bisher.)
            for plan in (watch_plan, export_plan):
                if not plan.path or plan.uses_credentials:
                    continue
                try:
                    await asyncio.to_thread(Path(plan.path).mkdir, parents=True, exist_ok=True)
                except OSError as exc:
                    raise HTTPException(400, f"{plan.label} kann nicht angelegt werden: {plan.path} ({exc.strerror or exc})")
        except HTTPException:
            if watch_task_stopped:
                await _restart_watch_task(request.app, settings)  # alte Einstellungen laufen weiter
            raise
        watch_dir, export_dir = watch_plan.path, export_plan.path

    await set_db_setting(session, "watch_dir", watch_dir)
    await set_db_setting(session, "export_dir", export_dir)
    await set_db_setting(session, "watch_dir_host", watch_host)
    await set_db_setting(session, "export_dir_host", export_host)
    for plan in (watch_plan, export_plan):
        keep = plan.network
        await set_db_setting(session, nfs.user_key(plan.target), plan.user if keep else "")
        await set_db_setting(session, nfs.password_key(plan.target), plan.token if keep else "")
    await set_db_setting(session, POLL_INTERVAL_KEY, str(interval))
    await session.commit()

    if mode == "docker":
        # Die Bind-Mounts entstehen erst beim Neustart (start.bat ->
        # generate-mounts.ps1); /app/external/* wird daher hier nicht angelegt.
        try:
            _write_host_mounts(settings, watch_host, export_host)
        except OSError:
            logger.warning("Konnte %s nicht schreiben", host_mounts_file(settings), exc_info=True)

    if _folder_changed(old_export, export_plan):
        await asyncio.to_thread(nfs.release_export)

    # Watch-Task neu starten, wenn sich Pfad, Zugangsdaten oder Intervall geaendert haben
    if watch_task_stopped or _watch_changed(old_watch, watch_plan) or (watch_plan.network and interval != old_interval):
        await _restart_watch_task(request.app, settings)
        logger.info("Watch-Ordner geaendert: %s -> %s", old_watch.path, watch_dir)

    return await _folder_settings_response(session, settings, restart_required=restart_required)


def _folder_changed(old: nfs.FolderConfig, plan: _FolderPlan) -> bool:
    return (
        old.path != plan.path
        or (plan.network and (old.user != plan.user or old.token != plan.token))
    )


def _watch_changed(old: nfs.FolderConfig, plan: _FolderPlan) -> bool:
    return _folder_changed(old, plan)


@router.post("/system/settings/test-folder", response_model=TestFolderResponse)
async def test_folder(
    body: TestFolderRequest,
    request: Request,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Prueft einen Ordner, ohne zu speichern.

    Laufwerksbuchstaben: nur Klassifikation (Netzlaufwerk -> UNC-Vorschlag),
    kein Netzwerkzugriff. UNC: mit den angegebenen (oder unveraendert
    gespeicherten) Zugangsdaten verbinden, lesen, Schreibtest, trennen.
    Antwortet immer mit 200 (ok=false + Grund); nie mit Zugangsdaten.
    """
    check_network_folder_rate(request)
    label = "Watch-Ordner" if body.target == nfs.WATCH else "Export-Ordner"
    if settings.deployment_mode == "docker":
        return TestFolderResponse(
            ok=False, kind="unsupported",
            message="Im Docker-Betrieb werden Ordner als Host-Pfad bzw. CIFS-Volume eingebunden (siehe Hilfe).",
        )
    path_str = _normalize_path_input(body.path)
    if not path_str:
        return TestFolderResponse(ok=False, kind="local", message="Bitte einen Ordner angeben.")

    def _fail(kind: str, exc: HTTPException) -> TestFolderResponse:
        detail = str(exc.detail)
        if detail.startswith(f"{label}: "):
            detail = detail[len(label) + 2:]
        return TestFolderResponse(ok=False, kind=kind, message=detail)

    if not _is_network_path(path_str):
        info = await _validate_in_thread(network_share.classify_drive, path_str)
        if info.kind in ("network_drive", "missing_drive", "unsuitable"):
            return TestFolderResponse(
                ok=False, kind=info.kind, message=network_share.drive_message(info),
                suggested_paths=list(info.suggestions),
            )
        try:
            validated = await _validate_in_thread(_validate_local_dir, path_str, label, settings)
        except HTTPException as exc:
            return _fail(info.kind if info.kind == "subst" else "local", exc)
        exists = await asyncio.to_thread(os.path.isdir, validated)
        message = "Ordner ist gültig." if exists else "Ordner ist gültig und wird beim Speichern angelegt."
        if info.kind == "subst":
            message = f"Ersatzlaufwerk {info.letter}: zeigt auf {validated} – gespeichert wird dieser Pfad. " + message
        return TestFolderResponse(ok=True, kind=info.kind if info.kind == "subst" else "local", message=message)

    stored = await nfs.load_folder_config(session, body.target, settings)
    try:
        plan = await _validate_in_thread(
            _plan_folder, body.target, path_str, body.net_user, body.net_password, stored, settings,
        )
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        return _fail("unc", exc)
    if not plan.uses_credentials:
        return TestFolderResponse(
            ok=True, kind="unc",
            message="Netzwerkpfad ohne Zugangsdaten (Experten-Einstellung ALLOW_NETWORK_FOLDERS) – nicht getestet.",
        )
    if not plan.stored_password and network_share.connections.in_use(plan.path):
        # SEC-NF-01: Die Freigabe wird schon mit gespeicherten Zugangsdaten
        # benutzt. Ein Test mit eingegebenen Zugangsdaten verglieche sie (ohne
        # NAS-Anmeldung) mit den gespeicherten — das Ergebnis verriete, ob das
        # Passwort stimmt. Deshalb kein Vergleich; geprueft wird beim Speichern.
        return TestFolderResponse(
            ok=True, kind="unc",
            message="Diese Freigabe wird bereits als Eingangs- bzw. Zielordner verwendet – "
                    "die eingegebenen Zugangsdaten werden beim Speichern geprüft (nicht getestet).",
        )
    try:
        message = await _probe_or_400(plan, create=False)
    except HTTPException as exc:
        return _fail("unc", exc)
    return TestFolderResponse(ok=True, kind="unc", message=message)


async def _stop_watch_task(app) -> None:
    """Stoppt den laufenden Watch-Task (gibt dabei seine Netzwerkverbindung frei)."""
    old_task = getattr(app.state, "watch_task", None)
    if old_task and not old_task.done():
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass


async def _restart_watch_task(app, settings: Settings) -> None:
    """Stoppt den laufenden Watch-Task und startet ihn mit dem neuen Pfad neu."""
    await _stop_watch_task(app)

    session_factory = getattr(app.state, "session_factory", None)
    if session_factory is None:
        logger.warning("session_factory nicht in app.state – Watch-Task kann nicht neu gestartet werden")
        return
    if getattr(app.state, "shutting_down", False):
        logger.info("Dienst wird beendet – Watch-Task wird nicht neu gestartet")
        return

    from app.services.watch_folder_service import run_watch_folder
    new_task = asyncio.create_task(run_watch_folder(session_factory, settings))
    app.state.watch_task = new_task
    logger.info("Watch-Ordner-Task neu gestartet")


# Speicher-Statistik (rglob+stat ueber das ganze Archiv, M-20) nur alle 60 s neu
# berechnen — die Einstellungsseite pollt /system/health alle 10 s.
_SYSTEM_INFO_TTL = 60.0
_system_info_cache: dict = {"key": None, "at": 0.0, "data": None}


async def _cached_system_info(settings: Settings) -> dict:
    key = (settings.DATABASE_URL, settings.ARCHIVE_DIR, settings.UPLOAD_DIR)
    now = time.monotonic()
    if (
        _system_info_cache["data"] is not None
        and _system_info_cache["key"] == key
        and now - _system_info_cache["at"] < _SYSTEM_INFO_TTL
    ):
        return _system_info_cache["data"]
    data = await asyncio.to_thread(get_system_info, settings)
    _system_info_cache.update(key=key, at=now, data=data)
    return data


def _reset_system_info_cache() -> None:
    """Nur fuer Tests."""
    _system_info_cache.update(key=None, at=0.0, data=None)


def _overall_status(components: dict) -> str:
    # Gleiches Vokabular wie /api/health: ok | warning | error.
    statuses = {c.get("status") for c in components.values()}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ok"


@router.get("/system/health")
async def system_health(
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Detaillierter System-Gesundheitscheck.

    Status-Vokabular wie /api/health: ``ok`` | ``warning`` (Funktion
    eingeschraenkt, z.B. Ollama/ChromaDB nicht erreichbar) | ``error``.
    """
    components = {}

    # Backend
    components["backend"] = {"status": "ok"}

    # Datenbank — S-LOW5: keine internen Exception-Details nach aussen.
    try:
        await session.execute(text("SELECT 1"))
        components["database"] = {"status": "ok"}
    except Exception:
        logger.exception("System-Health: Datenbank nicht erreichbar")
        components["database"] = {"status": "error", "message": "Datenbankfehler"}

    # Ollama: gleicher (gecachter) Zustand wie /api/health, dazu die
    # benoetigten Modelle mit Verfuegbarkeit und der letzte echte Aufruf.
    ollama = await ollama_health(settings)
    components["ollama"] = {
        "status": ollama.status,
        "message": ollama.message,
        "configured_models": ollama.configured_models(settings),
        "missing": ollama.probe.missing,
        "last_calls": get_last_outcomes(),
        "version": ollama.version,
        "min_version": OLLAMA_MIN_VERSION,
    }

    # ChromaDB (H1: Native/embedded hat keinen HTTP-Service — sonst meldet der
    # Health-Check dauerhaft "nicht erreichbar", obwohl die Vektorsuche laeuft)
    from app.services.vectorize_service import get_collection_count
    chroma_unreachable = {"status": "warning", "message": "Nicht erreichbar"}
    try:
        reachable = True
        if settings.CHROMADB_MODE != "embedded":
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://{settings.CHROMADB_HOST}:{settings.CHROMADB_PORT}/api/v2/heartbeat")
            if resp.status_code != 200:
                reachable = False
                components["chromadb"] = {"status": "warning", "message": f"HTTP {resp.status_code}"}
        if reachable:
            # In-process bzw. nach erfolgreichem Heartbeat: Count (blockierend ->
            # to_thread). None heisst Fehler — nicht als "0 Vektoren" ausgeben.
            vec_count = await asyncio.to_thread(get_collection_count, settings)
            components["chromadb"] = (
                chroma_unreachable if vec_count is None else {"status": "ok", "vectors": vec_count}
            )
    except Exception:
        components["chromadb"] = chroma_unreachable

    # Watch-/Export-Ordner: Erreichbarkeit (Netzwerkordner mit Grund) — nur
    # Pfad und Benutzername, nie Zugangsdaten.
    for target, name in ((nfs.WATCH, "watch_folder"), (nfs.EXPORT, "export_folder")):
        component = nfs.health_component(target)
        if component is not None:
            components[name] = component

    sys_info = await _cached_system_info(settings)

    # Dokument-Statistiken
    doc_count_result = await session.execute(
        select(func.count()).select_from(Document).where(Document.status != DocumentStatus.DELETED)
    )
    doc_count = doc_count_result.scalar() or 0

    # Installationspfad (vom Installer in den Datenordner geschrieben; install.ps1
    # schreibt mit Windows PowerShell 5.1 "-Encoding UTF8" = mit BOM -> utf-8-sig)
    install_path_file = settings.data_dir / ".install-path"
    try:
        install_path = install_path_file.read_text(encoding="utf-8-sig").strip() if install_path_file.exists() else ""
    except OSError:
        install_path = ""

    result = {
        "status": _overall_status(components),
        "app_version": read_app_version(),
        "deployment_mode": settings.deployment_mode,
        "components": components,
        "statistics": {
            "total_documents": doc_count,
            **sys_info,
        },
    }
    if install_path:
        result["install_path"] = install_path
    return result


class UpdateCheckResponse(BaseModel):
    enabled: bool
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str | None = None
    published_at: str | None = None
    notes: str | None = None
    checked_at: str | None = None
    cached: bool = False
    error: str | None = None


class UpdateCheckSettings(BaseModel):
    enabled: bool


UPDATE_CHECK_SETTING_KEY = "update_check_enabled"


async def _update_check_enabled(session: AsyncSession, settings: Settings) -> bool:
    """DB-Einstellung hat Vorrang; faellt auf den Config-Default zurueck."""
    raw = await get_db_setting(session, UPDATE_CHECK_SETTING_KEY, "")
    if raw == "":
        return settings.UPDATE_CHECK_ENABLED
    return raw == "true"


@router.get("/system/update-check", response_model=UpdateCheckResponse)
async def get_update_check(
    force: bool = False,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Opt-in Update-Pruefung. Macht nur dann einen externen Aufruf, wenn der
    Nutzer die Pruefung aktiviert hat."""
    from app.services import update_service

    if not await _update_check_enabled(session, settings):
        return UpdateCheckResponse(
            enabled=False, current_version=update_service.read_local_version()
        )
    data = await update_service.check_for_update(settings.UPDATE_MANIFEST_URL, force=force)
    return UpdateCheckResponse(
        enabled=True, checked_at=datetime.now(timezone.utc).isoformat(), **data
    )


@router.put("/system/update-check", response_model=UpdateCheckResponse)
async def set_update_check(
    body: UpdateCheckSettings,
    session: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Update-Pruefung aktivieren/deaktivieren (opt-in). Bei Aktivierung wird
    sofort einmal geprueft."""
    from app.services import update_service

    await set_db_setting(session, UPDATE_CHECK_SETTING_KEY, "true" if body.enabled else "false")
    await session.commit()
    if not body.enabled:
        return UpdateCheckResponse(
            enabled=False, current_version=update_service.read_local_version()
        )
    data = await update_service.check_for_update(settings.UPDATE_MANIFEST_URL, force=True)
    return UpdateCheckResponse(
        enabled=True, checked_at=datetime.now(timezone.utc).isoformat(), **data
    )


@router.post("/system/backup", status_code=202, response_model=TaskResponse)
async def create_backup_endpoint(
    full: bool = False,
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> TaskResponse:
    """Startet ein Backup als Hintergrund-Vorgang (HTTP 202 + Polling ueber
    GET /api/tasks/{id}). Ein Voll-Backup packt das ganze Archiv und kann
    Minuten dauern; es laeuft immer nur ein Backup gleichzeitig."""

    async def work(ctx: TaskContext) -> dict:
        await ctx.report_progress(
            None, "Erstelle Vollbackup (Datenbank + Dokumente) …" if full else "Erstelle Datenbank-Backup …"
        )
        try:
            # T17: create_backup() laeuft rekursiv ueber das Archiv und erzeugt
            # die ZIP — blockierender I/O, daher im Thread.
            path = Path(await asyncio.to_thread(create_backup, settings, full))
        except FileNotFoundError:
            logger.exception("Backup fehlgeschlagen: Datenbankdatei fehlt")
            raise TaskError("Backup fehlgeschlagen: Die Datenbankdatei wurde nicht gefunden.")
        except BackupSpaceError as exc:
            logger.warning("Backup abgelehnt: %s", exc)
            raise TaskError(f"Backup nicht gestartet. {exc}")
        except Exception:
            logger.exception("Backup fehlgeschlagen")
            raise TaskError("Backup fehlgeschlagen. Details stehen im Server-Log.")
        # AVAIL-05: dieselbe Aufbewahrungsregel wie das Auto-Backup — sonst
        # stapelten sich manuelle Voll-Backups bis zur vollen Platte.
        try:
            await asyncio.to_thread(cleanup_old_backups, settings)
        except Exception:
            logger.exception("Aufräumen alter Backups fehlgeschlagen")
        return {
            "filename": path.name,
            "is_full": full,
            "size_bytes": path.stat().st_size,
            "message": "Vollbackup erstellt" if full else "Backup erstellt",
        }

    task = await start_task(
        session_factory, "backup", work, key="global",
        conflict_message="Es läuft bereits ein Backup.",
    )
    return TaskResponse.model_validate(task)


@router.get("/system/backups")
async def list_backups_endpoint(settings: Settings = Depends(get_settings)):
    """Listet vorhandene Backups (neueste zuerst)."""
    return {"backups": await asyncio.to_thread(list_backups, settings)}


@router.get("/system/backups/{filename}")
async def download_backup(
    filename: str,
    settings: Settings = Depends(get_settings),
):
    """Backup-Datei herunterladen."""
    directory = backup_dir(settings)
    file_path = directory / filename
    # Path-Traversal-Schutz
    if not file_path.resolve().is_relative_to(directory.resolve()):
        raise HTTPException(400, "Ungültiger Dateiname")
    if not file_path.name.startswith("backup_") or not file_path.name.endswith(".zip") or not file_path.is_file():
        raise HTTPException(404, "Backup nicht gefunden")
    return FileResponse(file_path, filename=file_path.name, media_type="application/zip")


# AVAIL-06: VACUUM (exklusive Sperre) und FTS-Rebuild sind schwere
# Schreibvorgaenge ohne eigenen Guard — parallele Aufrufe blockierten alle
# Schreiber bis zum busy_timeout ("database is locked" im Queue-Worker/UI).
# Hoechstens EIN DB-Wartungslauf gleichzeitig; ein zweiter bekommt 409. Bewusst
# synchroner Vertrag wie bisher (Frontend wartet auf die Antwort).
_db_maintenance_running = False
DB_MAINTENANCE_BUSY = "Es läuft bereits eine Datenbank-Wartung. Bitte warten, bis sie fertig ist."


@contextmanager
def _exclusive_db_maintenance():
    global _db_maintenance_running
    if _db_maintenance_running:
        raise HTTPException(409, DB_MAINTENANCE_BUSY)
    _db_maintenance_running = True
    try:
        yield
    finally:
        _db_maintenance_running = False


@router.post("/system/maintenance/optimize-db")
async def optimize_db(settings: Settings = Depends(get_settings)):
    """Datenbank optimieren (VACUUM).

    VACUUM darf nicht in einer aktiven Transaktion laufen und blockiert alle
    Writes. Wir nutzen daher eine fresh sqlite3-Connection ausserhalb des
    SQLAlchemy-Pools und rufen sie in to_thread, um den Event-Loop nicht zu
    blockieren.
    """
    import sqlite3

    try:
        db_path = settings.sqlite_db_path
    except ValueError:
        raise HTTPException(500, "DB-Pfad konnte nicht ermittelt werden")

    def _vacuum() -> None:
        # autocommit; wartet wie die App-Verbindungen auf fremde Schreibsperren
        conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    try:
        with _exclusive_db_maintenance():
            await asyncio.to_thread(_vacuum)
        return {"message": "Datenbank optimiert"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("DB-Optimierung fehlgeschlagen")
        raise HTTPException(500, "Optimierung fehlgeschlagen")


@router.post("/system/maintenance/rebuild-index")
async def rebuild_index(session: AsyncSession = Depends(get_db, scope="function")):
    """FTS5-Suchindex neu aufbauen."""
    try:
        from app.services.search_service import rebuild_fts_index
        with _exclusive_db_maintenance():
            count = await rebuild_fts_index(session)
        return {"message": f"Index für {count} Dokumente neu aufgebaut"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Index-Rebuild fehlgeschlagen")
        raise HTTPException(500, "Index-Rebuild fehlgeschlagen")


@router.post("/system/maintenance/rebuild-vectors", status_code=202, response_model=TaskResponse)
async def rebuild_vectors(
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> TaskResponse:
    """Vektor-Index fuer alle Dokumente neu aufbauen — Hintergrund-Vorgang
    ``rebuild_vectors`` (HTTP 202 + Polling ueber GET /api/tasks/{id}); 409,
    solange schon ein Rebuild (auch die initiale Vektorisierung) laeuft."""
    from app.services.vectorize_service import start_vector_rebuild

    task = await start_vector_rebuild(session_factory, settings)
    return TaskResponse.model_validate(task)
