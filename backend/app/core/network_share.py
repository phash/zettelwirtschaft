r"""Netzwerkordner (UNC + Zugangsdaten) fuer den Native-Dienst unter Windows.

Der Native-Dienst laeuft als LocalSystem. Ein UNC-Zugriff ohne eigene
Verbindung meldet das *Computerkonto* am Server an (NTLM, FH-01) — die
Heim-NAS lehnt das meist ab, und ein Angreifer-Server bekaeme die
Maschinen-Anmeldung. Deshalb verbindet sich das Backend mit den Zugangsdaten,
die der Benutzer (PIN-Inhaber) in den Einstellungen eintraegt:
``WNetAddConnection2W`` mit ``CONNECT_TEMPORARY`` und ohne
Laufwerksbuchstaben. Jeder Zugriff auf einen UNC-Ordner laeuft ueber
``connections``: ``acquire`` verbindet, ``lease`` haelt die Verbindung fuer die
Dauer eines Zugriffs (auch ueber das Ende eines abgebrochenen asyncio-Tasks
hinaus, solange der Thread laeuft) — ohne Verbindungseintrag greift niemand auf
den Pfad zu, und getrennt wird erst, wenn kein Zugriff mehr laeuft.

NET-SEC-08: Der Server wird genau einmal aufgeloest und geprueft; verbunden
und zugegriffen wird ueber die geprueften IPv4-Adresse (``\\<ip>\freigabe``,
``access_path``). Der Redirector kann den Namen so nicht erneut (und anders)
aufloesen, und Windows-Verbindungseintraege gelten nur fuer genau diesen Namen.

Plattformneutral importierbar: ausserhalb von Windows wirft ``connect()``
``NetworkShareUnsupported``. Alle Funktionen blockieren (Netzwerk, Registry)
und gehoeren in ``asyncio.to_thread``.

Das Passwort steht nur in ``NetworkCredential`` (``repr`` maskiert) und wird
nie in Meldungen, Logs oder Ausnahmen uebernommen.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import stat
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# --- Windows-Konstanten -------------------------------------------------------
RESOURCETYPE_DISK = 1
CONNECT_TEMPORARY = 0x4
NO_ERROR = 0
ERROR_ACCESS_DENIED = 5
ERROR_BAD_NETPATH = 53
ERROR_NETNAME_DELETED = 64
ERROR_BAD_NET_NAME = 67
ERROR_INVALID_PASSWORD = 86
ERROR_SEM_TIMEOUT = 121
ERROR_NO_NET_OR_BAD_PATH = 1203
ERROR_EXTENDED_ERROR = 1208
ERROR_SESSION_CREDENTIAL_CONFLICT = 1219
ERROR_NO_NETWORK = 1222
ERROR_CONNECTION_ABORTED = 1236
ERROR_NOT_AUTHENTICATED = 1244
ERROR_LOGON_FAILURE = 1326
ERROR_ACCOUNT_RESTRICTION = 1327
ERROR_PASSWORD_EXPIRED = 1330
ERROR_ACCOUNT_DISABLED = 1331
ERROR_ACCOUNT_LOCKED_OUT = 1909
ERROR_BAD_USERNAME = 2202
ERROR_NOT_CONNECTED = 2250
NERR_NET_NAME_NOT_FOUND = 2310
ERROR_OPEN_FILES = 2401

# WNetGetProviderNameW: Netzwerktyp des SMB-Clients (LanmanWorkstation)
WNNC_NET_LANMAN = 0x00020000
# NetShareGetInfo Level 1005: Freigabe ist DFS-Link bzw. DFS-Wurzel
SHI1005_FLAGS_DFS = 0x01
SHI1005_FLAGS_DFS_ROOT = 0x02

DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

MESSAGE_PREFIX = "Netzwerkordner: "

_MESSAGES: dict[int, str] = {
    ERROR_ACCESS_DENIED: "Zugriff verweigert – der Benutzer hat keine Rechte auf diese Freigabe.",
    ERROR_BAD_NETPATH: "Server nicht gefunden – Name/IP-Adresse prüfen, ist das Gerät eingeschaltet?",
    ERROR_BAD_NET_NAME: "Die Freigabe gibt es auf dem Server nicht – Schreibweise prüfen.",
    ERROR_NETNAME_DELETED: "Die Verbindung wurde unterbrochen.",
    ERROR_INVALID_PASSWORD: "Das Passwort ist falsch.",
    ERROR_SEM_TIMEOUT: "Zeitüberschreitung beim Verbinden.",
    ERROR_CONNECTION_ABORTED: "Zeitüberschreitung beim Verbinden.",
    ERROR_NO_NET_OR_BAD_PATH: "Netzwerkpfad nicht erreichbar.",
    ERROR_SESSION_CREDENTIAL_CONFLICT: (
        "Zu diesem Server besteht schon eine Verbindung mit einem anderen Benutzer – für "
        "Watch- und Export-Ordner auf demselben Server denselben Benutzer verwenden."
    ),
    ERROR_NO_NETWORK: "Kein Netzwerk verfügbar.",
    ERROR_NOT_AUTHENTICATED: "Nicht angemeldet.",
    ERROR_LOGON_FAILURE: "Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch.",
    ERROR_ACCOUNT_RESTRICTION: (
        "Konto-Einschränkung (z.B. Konto ohne Passwort ist für Netzwerkzugriff gesperrt)."
    ),
    ERROR_PASSWORD_EXPIRED: "Passwort abgelaufen.",
    ERROR_ACCOUNT_DISABLED: "Konto deaktiviert.",
    ERROR_ACCOUNT_LOCKED_OUT: "Konto gesperrt.",
    ERROR_BAD_USERNAME: "Ungültiger Benutzername.",
    ERROR_NOT_CONNECTED: "Keine Verbindung zur Freigabe.",
}

_PROVIDER_TEXT_MAX = 200


def message_for(code: int, provider_text: str = "") -> str:
    """Deutsche Meldung fuer einen WNet-/Win32-Fehlercode (mit Praefix)."""
    if code == ERROR_EXTENDED_ERROR and provider_text:
        text = " ".join(provider_text.split())[:_PROVIDER_TEXT_MAX]
        return f"{MESSAGE_PREFIX}{text}"
    text = _MESSAGES.get(code)
    if text is None:
        text = f"Verbindung fehlgeschlagen (Windows-Fehler {code})."
    return f"{MESSAGE_PREFIX}{text}"


class ShareError(Exception):
    """Verbindungs-/Zugriffsfehler mit deutscher Meldung (``str(exc)``).

    Enthaelt nie Zugangsdaten — der Provider-Text wird vor dem Speichern um
    das Passwort bereinigt (``connect``)."""

    def __init__(self, code: int, provider_text: str = "", message: str | None = None):
        self.code = code
        self.message = message or message_for(code, provider_text)
        super().__init__(self.message)


class NetworkShareUnsupported(ShareError):
    def __init__(self) -> None:
        super().__init__(
            -1, message=f"{MESSAGE_PREFIX}Netzwerkordner mit Zugangsdaten werden nur unter Windows unterstützt.",
        )


class UncPathError(ValueError):
    """Unzulaessige UNC-Form (deutsche Meldung, ohne Label)."""


class HostNotFound(UncPathError):
    """Server-Name nicht aufloesbar — es wird nicht verbunden (fail closed)."""


@dataclass(frozen=True)
class NetworkCredential:
    user: str
    password: str = field(repr=False)

    def __repr__(self) -> str:  # nie das Passwort
        return f"NetworkCredential(user={self.user!r}, password='***')"

    __str__ = __repr__

    def same_as(self, other: "NetworkCredential | None") -> bool:
        return other is not None and _key(self.user) == _key(other.user) and self.password == other.password


# --- UNC-Form ------------------------------------------------------------------

_HOST_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*$")
_INVALID_SEGMENT_CHARS = set('<>:"|?*')
_SHARE_MAX = 80


@dataclass(frozen=True)
class UncPath:
    host: str
    share: str
    rest: tuple[str, ...]

    @property
    def root(self) -> str:
        return f"\\\\{self.host}\\{self.share}"

    @property
    def path(self) -> str:
        return "\\".join([self.root, *self.rest])


def is_unc(path_str: str) -> bool:
    return (path_str or "").replace("/", "\\").startswith("\\\\")


def _key(value: str) -> str:
    return value.casefold()


def share_key(path_str: str) -> str | None:
    r"""Vergleichsschluessel ``\\host\share`` (casefold) oder None."""
    try:
        return _key(parse_unc(path_str).root)
    except UncPathError:
        return None


def parse_unc(path_str: str) -> UncPath:
    r"""``\\host\share[\sub...]`` zerlegen und die Form pruefen (ohne Netzwerk).

    Abgelehnt: Geraete-Praefixe, leere Segmente, ``.``/``..``, WebDAV
    (``@``, ``DavWWWRoot``), IPv6-Literale und Ports, administrative
    Freigaben (``C$``, ``IPC$`` ...) und ungueltige Zeichen. Segmente mit
    Punkt oder Leerzeichen am Ende sind mehrdeutig (Win32 entfernt sie:
    ``C$ `` wuerde zu ``C$``) und werden abgelehnt (NET-SEC-10).
    """
    p = (path_str or "").replace("/", "\\")
    if not p.startswith("\\\\"):
        raise UncPathError(f"Kein Netzwerkpfad (\\\\server\\freigabe): {path_str}")
    if p.startswith(("\\\\?\\", "\\\\.\\")):
        raise UncPathError(f"Pfade mit \\\\?\\ oder \\\\.\\ sind nicht erlaubt: {path_str}")
    parts = p[2:].rstrip("\\").split("\\")
    if len(parts) < 2 or any(not seg.strip() for seg in parts):
        raise UncPathError(f"Netzwerkpfad muss die Form \\\\server\\freigabe\\ordner haben: {path_str}")
    host, share, *rest = parts
    if any(seg in (".", "..") for seg in parts):
        raise UncPathError(f"Netzwerkpfad enthält unerlaubte Pfad-Segmente: {path_str}")
    if any(seg.endswith((".", " ")) for seg in parts):
        raise UncPathError(f"Ordner- und Freigabenamen dürfen nicht auf Punkt oder Leerzeichen enden: {path_str}")
    if "@" in host or any(_key(seg) == "davwwwroot" for seg in parts):
        raise UncPathError(f"WebDAV-Adressen werden nicht unterstützt – bitte eine SMB-Freigabe angeben: {path_str}")
    if host.startswith("[") or ":" in host or _key(host).endswith(".ipv6-literal.net"):
        raise UncPathError(f"IPv6-Adressen und Ports werden nicht unterstützt – bitte Name oder IPv4-Adresse angeben: {path_str}")
    if not _HOST_RE.match(host):
        raise UncPathError(f"Ungültiger Servername: {host}")
    if is_admin_share(share):
        raise UncPathError(
            f"Administrative Freigaben (C$, ADMIN$, IPC$) sind nicht erlaubt – bitte eine normale Freigabe angeben: {path_str}"
        )
    if len(share) > _SHARE_MAX or any(ch in _INVALID_SEGMENT_CHARS for seg in parts[1:] for ch in seg):
        raise UncPathError(f"Ungültiger Freigabe- oder Ordnername: {path_str}")
    return UncPath(host=host, share=share, rest=tuple(rest))


def is_admin_share(share: str) -> bool:
    """``C$``, ``ADMIN$``, ``IPC$`` ... — auch mit Punkt/Leerzeichen am Ende."""
    return share.rstrip(" .").endswith("$")


# --- Rechner-eigene Namen/Adressen -----------------------------------------------

_LOCAL_HOST_MESSAGE = (
    "Freigaben auf diesem Rechner und administrative Freigaben (C$, ADMIN$) sind nicht erlaubt "
    "— bitte den lokalen Pfad angeben"
)
_HOST_NOT_FOUND_MESSAGE = "Server nicht gefunden – Name/IP-Adresse prüfen, ist das Gerät eingeschaltet?"
_NO_IPV4_MESSAGE = "Der Server hat keine IPv4-Adresse – bitte die IPv4-Adresse des Servers angeben."


def _resolve_addresses(host: str) -> list[str]:
    """DNS/NetBIOS-Aufloesung (keine SMB-Anmeldung), Reihenfolge wie
    ``getaddrinfo``. Leere Liste = nicht aufloesbar. In Tests ersetzt."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return []
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


def local_host_names() -> frozenset[str]:
    """Namen/Adressen, unter denen dieser Rechner sich selbst per SMB erreicht.

    Bewusst ohne Cache (NET-SEC-08): nach einem DHCP-Wechsel oder einem neuen
    Adapter (VPN) muss die neue eigene Adresse sofort erkannt werden."""
    names = {"localhost", ".", "::1", "0.0.0.0"}
    try:
        name = socket.gethostname().lower()
        names |= {name, name.split(".")[0], socket.getfqdn().lower()}
        names |= {a.lower() for a in socket.gethostbyname_ex(name)[2]}
    except OSError:
        pass
    return frozenset(names)


def _own_addresses() -> frozenset[str]:
    try:
        addresses = _resolve_addresses(socket.gethostname())
    except OSError:
        addresses = []
    return frozenset(a.split("%")[0].lower() for a in addresses)


def is_local_host_name(host: str) -> bool:
    """Reine Namenspruefung (ohne DNS)."""
    h = host.lower().strip("[]")
    return h in local_host_names() or h.startswith("127.") or h.endswith(".localhost")


def resolve_host(host: str) -> list[str]:
    """Loest den Host auf und prueft jede Adresse (INFRA-02, NET-SEC-08).

    Lehnt Hosts ab, die auf DIESEN Rechner zeigen (Name, Loopback, eigene
    Adresse — auch per DNS-Alias wie ``meinpc.fritz.box``), und Hosts, die sich
    nicht aufloesen lassen (fail closed: was hier nicht geprueft werden kann,
    darf der SMB-Client auch nicht auf anderem Weg aufloesen). Liefert die
    geprueften Adressen in Aufloesungsreihenfolge.
    """
    if is_local_host_name(host):
        raise UncPathError(_LOCAL_HOST_MESSAGE)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    raw_addresses = [host] if literal is not None else _resolve_addresses(host)
    if not raw_addresses:
        raise HostNotFound(_HOST_NOT_FOUND_MESSAGE)
    own = _own_addresses()
    local_names = local_host_names()
    addresses: list[str] = []
    for raw in raw_addresses:
        addr_str = raw.split("%")[0].lower()
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_unspecified or addr_str in own or addr_str in local_names:
            raise UncPathError(_LOCAL_HOST_MESSAGE)
        addresses.append(addr_str)
    if not addresses:
        raise HostNotFound(_HOST_NOT_FOUND_MESSAGE)
    return addresses


def check_host_not_local(host: str) -> None:
    """Pruefung vor dem Speichern (ohne SMB): siehe ``resolve_host``."""
    resolve_host(host)


def pinned_address(host: str) -> str:
    """Erste gepruefte IPv4-Adresse des Hosts — ueber sie wird verbunden."""
    for addr in resolve_host(host):
        if ipaddress.ip_address(addr).version == 4:
            return addr
    raise HostNotFound(_NO_IPV4_MESSAGE)


def _reset_caches() -> None:
    """Kompatibilitaet fuer Tests (es gibt keine Caches mehr)."""


# --- Windows-API (Fassade, in Tests ersetzbar) --------------------------------------

class _UnsupportedApi:
    """Nicht-Windows: keine Netzwerkverbindungen, keine Laufwerksbuchstaben."""

    def add(self, remote: str, user: str, password: str) -> int:
        raise NetworkShareUnsupported()

    def cancel(self, remote: str, force: bool = False) -> int:
        return ERROR_NOT_CONNECTED

    def share_flags(self, server: str, share: str) -> int | None:
        """``shi1005_flags`` der Freigabe (DFS-Erkennung) oder None (unbekannt)."""
        return None

    def last_error_text(self) -> str:
        return ""

    def connected_remotes(self) -> set[str]:
        return set()

    def get_connection(self, local: str) -> str | None:
        return None

    def drive_type(self, root: str) -> int:
        return DRIVE_UNKNOWN

    def query_dos_device(self, device: str) -> str | None:
        return None

    def console_user_sid(self) -> str | None:
        """SID des an der Konsole angemeldeten Benutzers (SEC-NF-03)."""
        return None

    def read_user_remote_path(self, sid: str, letter: str) -> str | None:
        r"""``HKU\<sid>\Network\<X>\RemotePath`` (nur dieser eine Benutzer)."""
        return None

    def net_share_get_info(self, name: str) -> tuple[bool, str | None]:
        return (False, None)

    def read_scanner_registry(self) -> dict[str, str]:
        return {}


class _WinApi(_UnsupportedApi):  # pragma: no cover - echte Win32-Aufrufe, in Tests ersetzt
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        self._ctypes = ctypes
        self._w = w

        class NETRESOURCEW(ctypes.Structure):
            _fields_ = [
                ("dwScope", w.DWORD), ("dwType", w.DWORD), ("dwDisplayType", w.DWORD),
                ("dwUsage", w.DWORD), ("lpLocalName", w.LPWSTR), ("lpRemoteName", w.LPWSTR),
                ("lpComment", w.LPWSTR), ("lpProvider", w.LPWSTR),
            ]

        class USE_INFO_0(ctypes.Structure):
            _fields_ = [("ui0_local", w.LPWSTR), ("ui0_remote", w.LPWSTR)]

        class SHARE_INFO_1005(ctypes.Structure):
            _fields_ = [("shi1005_flags", w.DWORD)]

        class SHARE_INFO_2(ctypes.Structure):
            _fields_ = [
                ("shi2_netname", w.LPWSTR), ("shi2_type", w.DWORD), ("shi2_remark", w.LPWSTR),
                ("shi2_permissions", w.DWORD), ("shi2_max_uses", w.DWORD),
                ("shi2_current_uses", w.DWORD), ("shi2_path", w.LPWSTR), ("shi2_passwd", w.LPWSTR),
            ]

        self.NETRESOURCEW = NETRESOURCEW
        self.USE_INFO_0 = USE_INFO_0
        self.SHARE_INFO_2 = SHARE_INFO_2
        self.SHARE_INFO_1005 = SHARE_INFO_1005

        mpr = ctypes.WinDLL("mpr.dll", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        net = ctypes.WinDLL("netapi32.dll", use_last_error=True)
        self._k32 = k32
        self._mpr = mpr

        self._add = mpr.WNetAddConnection2W
        self._add.argtypes = [ctypes.POINTER(NETRESOURCEW), w.LPCWSTR, w.LPCWSTR, w.DWORD]
        self._add.restype = w.DWORD
        self._cancel = mpr.WNetCancelConnection2W
        self._cancel.argtypes = [w.LPCWSTR, w.DWORD, w.BOOL]
        self._cancel.restype = w.DWORD
        self._get_connection = mpr.WNetGetConnectionW
        self._get_connection.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        self._get_connection.restype = w.DWORD
        self._last_error = mpr.WNetGetLastErrorW
        self._last_error.argtypes = [ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD, w.LPWSTR, w.DWORD]
        self._last_error.restype = w.DWORD
        self._drive_type = k32.GetDriveTypeW
        self._drive_type.argtypes = [w.LPCWSTR]
        self._drive_type.restype = w.UINT
        self._query_dos = k32.QueryDosDeviceW
        self._query_dos.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        self._query_dos.restype = w.DWORD
        self._use_enum = net.NetUseEnum
        self._use_enum.argtypes = [
            w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), w.DWORD,
            ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD),
        ]
        self._use_enum.restype = w.DWORD
        self._share_get_info = net.NetShareGetInfo
        self._share_get_info.argtypes = [w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        self._share_get_info.restype = w.DWORD
        self._buffer_free = net.NetApiBufferFree
        self._buffer_free.argtypes = [ctypes.c_void_p]
        self._buffer_free.restype = w.DWORD
        self._provider: str | None = None

    def _smb_provider(self) -> str | None:
        """Name des SMB-Providers (lokalisiert, z.B. "Microsoft Windows Network")."""
        if self._provider is None:
            w = self._w
            fn = self._mpr.WNetGetProviderNameW
            fn.argtypes = [w.DWORD, w.LPWSTR, self._ctypes.POINTER(w.DWORD)]
            fn.restype = w.DWORD
            size = w.DWORD(256)
            buf = self._ctypes.create_unicode_buffer(size.value)
            if fn(WNNC_NET_LANMAN, buf, self._ctypes.byref(size)) == NO_ERROR and buf.value:
                self._provider = buf.value
        return self._provider

    def add(self, remote: str, user: str, password: str) -> int:
        res = self.NETRESOURCEW()
        res.dwType = RESOURCETYPE_DISK
        res.lpLocalName = None  # ohne Laufwerksbuchstaben
        res.lpRemoteName = remote
        # NET-SEC-10: nur der SMB-Provider, nie WebDAV (WebClient) als Ausweichweg.
        res.lpProvider = self._smb_provider()
        return int(self._add(self._ctypes.byref(res), password, user, CONNECT_TEMPORARY))

    def cancel(self, remote: str, force: bool = False) -> int:
        return int(self._cancel(remote, 0, bool(force)))

    def share_flags(self, server: str, share: str) -> int | None:
        # Laeuft ueber die schon bestehende SMB-Sitzung zum Server (IPC$), greift
        # nicht auf die Freigabe selbst zu und folgt keiner DFS-Weiterleitung.
        ctypes = self._ctypes
        buf = ctypes.c_void_p()
        rc = self._share_get_info(server, share, 1005, ctypes.byref(buf))
        try:
            if rc == NO_ERROR and buf.value:
                return int(ctypes.cast(buf, ctypes.POINTER(self.SHARE_INFO_1005)).contents.shi1005_flags)
            return None
        finally:
            if buf.value:
                self._buffer_free(buf)

    def last_error_text(self) -> str:
        w = self._w
        err = w.DWORD(0)
        buf = self._ctypes.create_unicode_buffer(512)
        prov = self._ctypes.create_unicode_buffer(256)
        if self._last_error(self._ctypes.byref(err), buf, len(buf), prov, len(prov)) != NO_ERROR:
            return ""
        return buf.value

    def connected_remotes(self) -> set[str]:
        ctypes, w = self._ctypes, self._w
        buf = ctypes.c_void_p()
        read, total, resume = w.DWORD(0), w.DWORD(0), w.DWORD(0)
        rc = self._use_enum(
            None, 0, ctypes.byref(buf), 0xFFFFFFFF, ctypes.byref(read), ctypes.byref(total), ctypes.byref(resume),
        )
        try:
            if rc != NO_ERROR or not buf.value:
                return set()
            entries = ctypes.cast(buf, ctypes.POINTER(self.USE_INFO_0 * read.value)).contents
            return {_key(e.ui0_remote) for e in entries if e.ui0_remote}
        finally:
            if buf.value:
                self._buffer_free(buf)

    def get_connection(self, local: str) -> str | None:
        w = self._w
        size = w.DWORD(1024)
        buf = self._ctypes.create_unicode_buffer(size.value)
        if self._get_connection(local, buf, self._ctypes.byref(size)) != NO_ERROR:
            return None
        return buf.value or None

    def drive_type(self, root: str) -> int:
        return int(self._drive_type(root))

    def query_dos_device(self, device: str) -> str | None:
        buf = self._ctypes.create_unicode_buffer(1024)
        if not self._query_dos(device, buf, len(buf)):
            return None
        return buf.value or None

    def console_user_sid(self) -> str | None:
        ctypes, w = self._ctypes, self._w
        k32 = self._k32
        k32.WTSGetActiveConsoleSessionId.restype = w.DWORD
        session = k32.WTSGetActiveConsoleSessionId()
        if session == 0xFFFFFFFF:
            return None
        wts = ctypes.WinDLL("wtsapi32.dll", use_last_error=True)
        query = wts.WTSQuerySessionInformationW
        query.argtypes = [w.HANDLE, w.DWORD, ctypes.c_int, ctypes.POINTER(w.LPWSTR), ctypes.POINTER(w.DWORD)]
        query.restype = w.BOOL
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]

        def _info(info_class: int) -> str:
            buf, size = w.LPWSTR(), w.DWORD(0)
            if not query(None, session, info_class, ctypes.byref(buf), ctypes.byref(size)):
                return ""
            try:
                return buf.value or ""
            finally:
                wts.WTSFreeMemory(ctypes.cast(buf, ctypes.c_void_p))

        user, domain = _info(5), _info(7)  # WTSUserName, WTSDomainName
        if not user:
            return None
        advapi = ctypes.WinDLL("advapi32.dll", use_last_error=True)
        lookup = advapi.LookupAccountNameW
        lookup.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(w.DWORD), w.LPWSTR,
                           ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD)]
        lookup.restype = w.BOOL
        account = f"{domain}\\{user}" if domain else user
        sid_size, dom_size, use = w.DWORD(0), w.DWORD(0), w.DWORD(0)
        lookup(None, account, None, ctypes.byref(sid_size), None, ctypes.byref(dom_size), ctypes.byref(use))
        if not sid_size.value:
            return None
        sid_buf = ctypes.create_string_buffer(sid_size.value)
        dom_buf = ctypes.create_unicode_buffer(max(dom_size.value, 1))
        if not lookup(None, account, sid_buf, ctypes.byref(sid_size), dom_buf, ctypes.byref(dom_size), ctypes.byref(use)):
            return None
        convert = advapi.ConvertSidToStringSidW
        convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
        convert.restype = w.BOOL
        text = w.LPWSTR()
        if not convert(sid_buf, ctypes.byref(text)):
            return None
        try:
            return text.value
        finally:
            k32.LocalFree.argtypes = [ctypes.c_void_p]
            k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    def read_user_remote_path(self, sid: str, letter: str) -> str | None:
        import winreg

        if not sid.startswith("S-1-5-21-") or "\\" in sid or not re.fullmatch(r"[A-Za-z]", letter):
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_USERS, f"{sid}\\Network\\{letter}") as key:
                value, _ = winreg.QueryValueEx(key, "RemotePath")
        except OSError:
            return None
        return value if isinstance(value, str) and value else None

    def net_share_get_info(self, name: str) -> tuple[bool, str | None]:
        ctypes = self._ctypes
        buf = ctypes.c_void_p()
        rc = self._share_get_info(None, name, 2, ctypes.byref(buf))
        try:
            if rc == NO_ERROR and buf.value:
                info = ctypes.cast(buf, ctypes.POINTER(self.SHARE_INFO_2)).contents
                return (True, info.shi2_path)
        finally:
            if buf.value:
                self._buffer_free(buf)
        if rc == ERROR_ACCESS_DENIED:
            # Level 2 braucht Adminrechte (Dev-Betrieb): Level 1 nennt nur die Existenz.
            buf = ctypes.c_void_p()
            rc = self._share_get_info(None, name, 1, ctypes.byref(buf))
            if buf.value:
                self._buffer_free(buf)
            return (rc == NO_ERROR, None)
        return (False, None)

    def read_scanner_registry(self) -> dict[str, str]:
        import winreg

        values: dict[str, str] = {}
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Zettelwirtschaft", 0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                for name in SCANNER_REGISTRY_VALUES:
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    if isinstance(value, str):
                        values[name] = value
        except OSError:
            pass
        return values


SCANNER_REGISTRY_VALUES = ("ScannerShareName", "ScannerShareUser", "ScannerSharePath")


def _make_api() -> _UnsupportedApi:
    if IS_WINDOWS:
        try:
            return _WinApi()
        except (OSError, AttributeError):  # pragma: no cover
            logger.warning("Windows-Netzwerk-API nicht verfügbar", exc_info=True)
    return _UnsupportedApi()


_api: _UnsupportedApi = _make_api()


def supported() -> bool:
    """True, wenn Netzwerkverbindungen/Laufwerksabfragen moeglich sind (Windows)."""
    return type(_api) is not _UnsupportedApi


# --- Verbinden / Trennen -----------------------------------------------------------

DFS_MESSAGE = (
    f"{MESSAGE_PREFIX}Die Freigabe ist eine DFS-Weiterleitung – das wird nicht unterstützt, "
    "bitte den Zielpfad direkt angeben."
)


def access_root_for(unc: UncPath, address: str) -> str:
    return f"\\\\{address}\\{unc.share}"


def resolve_for_connect(host: str) -> str:
    """Gepruefte IPv4-Adresse fuer einen Verbindungsaufbau (ShareError mit Meldung)."""
    try:
        return pinned_address(host)
    except HostNotFound as exc:
        raise ShareError(ERROR_BAD_NETPATH, message=f"{MESSAGE_PREFIX}{exc}") from None
    except UncPathError as exc:
        raise ShareError(ERROR_ACCESS_DENIED, message=f"{MESSAGE_PREFIX}{exc}") from None


def connect(root: str, cred: NetworkCredential, *, stale_cleanup: bool = True, address: str | None = None) -> str:
    r"""Verbindet ``\\host\share`` mit den Zugangsdaten (ohne Laufwerksbuchstaben).

    Loest den Host genau einmal auf, prueft ihn (DNS kann sich seit dem
    Speichern geaendert haben) und verbindet ueber die gepruefte IPv4-Adresse.
    Liefert die Zugriffswurzel ``\\<ip>\share`` — alle Dateizugriffe muessen
    sie verwenden (``ShareConnections.access_path``).

    ``stale_cleanup``: nur vor dem ERSTEN Verbinden wird eine evtl. aus einem
    Absturz verbliebene Verbindung getrennt — nie beim Wiederverbinden unter
    laufenden Zugriffen (NET-SEC-02). DFS-Freigaben werden ohne Zugriff auf die
    Freigabe erkannt und abgelehnt (NET-SEC-09), bei jedem (Wieder-)Verbinden.
    ``address``: schon gepruefte Adresse (``ShareConnections.acquire``).
    """
    unc = parse_unc(root)
    if not cred.user or not cred.password:
        raise ShareError(ERROR_LOGON_FAILURE, message=f"{MESSAGE_PREFIX}Benutzer und Passwort der Freigabe fehlen.")
    if address is None:
        address = resolve_for_connect(unc.host)
    access_root = access_root_for(unc, address)
    if stale_cleanup:
        _api.cancel(access_root, True)  # idempotent, 2250 (nicht verbunden) ist normal
    rc = _api.add(access_root, cred.user, cred.password)
    if rc != NO_ERROR:
        provider = _api.last_error_text() if rc == ERROR_EXTENDED_ERROR else ""
        if provider and cred.password:
            provider = provider.replace(cred.password, "***")
        raise ShareError(rc, provider)
    flags = _api.share_flags(f"\\\\{address}", unc.share)
    if flags is not None and flags & (SHI1005_FLAGS_DFS | SHI1005_FLAGS_DFS_ROOT):
        _api.cancel(access_root, True)
        raise ShareError(0, message=DFS_MESSAGE)
    return access_root


def disconnect(access_root: str, force: bool = False) -> None:
    """Trennt; ``force`` nur beim Beenden (schliesst offene Dateien)."""
    rc = _api.cancel(access_root, force)
    if rc not in (NO_ERROR, ERROR_NOT_CONNECTED):
        logger.warning("Netzwerkverbindung %s konnte nicht getrennt werden (Windows-Fehler %s)", access_root, rc)


def is_connected(access_root: str) -> bool:
    return _key(access_root) in _api.connected_remotes()


BUSY_MESSAGE = (
    f"{MESSAGE_PREFIX}Frühere Zugriffe auf die Freigabe laufen noch – neuer Verbindungsversuch später."
)
CLOSED_MESSAGE = f"{MESSAGE_PREFIX}Der Dienst wird beendet – keine neuen Verbindungen."
_SHUTDOWN_WAIT = 30.0


@dataclass(eq=False)
class _Entry:
    key: str             # casefold(\\<ip>\share) — eine Windows-Verbindung je Adresse+Freigabe
    root: str            # \\host\share wie zuerst konfiguriert (Anzeige)
    host: str            # gepruefte IPv4-Adresse (SMB: ein Benutzer je Server)
    cred: NetworkCredential
    share: str = ""      # casefold(Freigabename)
    names: set[str] = field(default_factory=set)  # casefold(\\name\share), unter denen sie gehalten wird
    targets: set[str] = field(default_factory=set)
    leases: int = 0
    access_root: str = ""  # \\<gepruefte IPv4>\share — nur darueber wird zugegriffen
    connected: bool = False
    broken: bool = False   # Verbindungseintrag verschwunden, Zugriffe laufen noch
    ever_connected: bool = False


class Lease:
    """Haelt eine Verbindung fuer die Dauer eines Zugriffs (``with``/``close``)."""

    def __init__(self, manager: "ShareConnections | None", entry: _Entry | None) -> None:
        self._manager = manager
        self._entry = entry
        self._closed = False

    @property
    def access_root(self) -> str:
        return self._entry.access_root if self._entry else ""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._manager is not None and self._entry is not None:
            self._manager._end_lease(self._entry)

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


NO_LEASE = Lease(None, None)


class ShareConnections:
    r"""Verbindungen pro Prozess, Schluessel ``\\host\share`` (casefold).

    - Referenzzaehlung je Ziel (``watch``, ``export``, ``probe:<id>``) und je
      laufendem Zugriff (``lease``): getrennt wird erst, wenn das letzte Ziel
      freigegeben UND der letzte Zugriff beendet ist (NET-SEC-02/03). Ein Thread,
      der noch auf den Pfad zugreift, behaelt so immer die eigene Verbindung —
      Windows meldet sonst das Computerkonto an (FH-01).
    - Je Host nur ein Benutzer (SMB: ein Satz Zugangsdaten pro Server und
      Anmeldesitzung, Fehler 1219) — ein Konflikt wird ohne WNet-Aufruf gemeldet.
    - Fehlt ein Verbindungseintrag (``check_connected``), wird NICHT unter
      laufenden Zugriffen neu verbunden: der Eintrag gilt als defekt, neue
      Zugriffe werden abgelehnt, und erst wenn alle Zugriffe beendet sind, darf
      ``acquire`` neu verbinden.
    - Die globale Sperre schuetzt nur die Buchhaltung; die blockierenden
      WNet-Aufrufe laufen unter einer Sperre je Freigabe (R7) — ein toter Host
      haelt die anderen Freigaben nicht auf.
    - Nach ``shutdown`` werden keine Verbindungen mehr aufgebaut.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.RLock())
        self._entries: dict[str, _Entry] = {}
        self._key_locks: dict[str, threading.Lock] = {}
        self._closed = False

    # --- intern -----------------------------------------------------------------
    def _key_lock(self, key: str) -> threading.Lock:
        with self._cond:
            return self._key_locks.setdefault(key, threading.Lock())

    def _drop_target_locked(self, entry: _Entry, target: str) -> bool:
        """Ziel austragen; True, wenn der Eintrag danach getrennt werden muss."""
        entry.targets.discard(target)
        if entry.targets:
            return False
        if entry.leases:
            logger.info(
                "Netzwerkverbindung %s: %d Zugriff(e) laufen noch – Trennen aufgeschoben", entry.root, entry.leases,
            )
            return False
        if self._entries.get(entry.key) is entry:
            del self._entries[entry.key]
        return True

    def _disconnect_entry(self, entry: _Entry, force: bool = False) -> None:
        with self._key_lock(entry.key):
            with self._cond:
                current = self._entries.get(entry.key)
                # Inzwischen neu belegt (gleiche Freigabe): die Verbindung gehoert
                # dem neuen Eintrag; dessen erstes Verbinden raeumt ohnehin auf.
                if current is not None and current is not entry:
                    return
                was_connected, entry.connected = entry.connected, False
            if was_connected and entry.access_root:
                disconnect(entry.access_root, force)
                logger.info("Netzwerkverbindung getrennt: %s", entry.root)

    def _end_lease(self, entry: _Entry) -> None:
        with self._cond:
            entry.leases -= 1
            self._cond.notify_all()
            drop = entry.leases <= 0 and not entry.targets and self._entries.get(entry.key) is entry
            if drop:
                del self._entries[entry.key]
        if drop:
            self._disconnect_entry(entry)

    def _connect_entry(self, entry: _Entry) -> None:
        with self._key_lock(entry.key):
            with self._cond:
                if self._closed:
                    raise ShareError(ERROR_NOT_CONNECTED, message=CLOSED_MESSAGE)
                if entry.connected and not entry.broken:
                    if is_connected(entry.access_root):
                        return
                    entry.broken = True
                if entry.broken and entry.leases:
                    raise ShareError(ERROR_NOT_CONNECTED, message=BUSY_MESSAGE)
                first = not entry.ever_connected
            access_root = connect(entry.root, entry.cred, stale_cleanup=first, address=entry.host)
            with self._cond:
                entry.access_root = access_root
                entry.connected = True
                entry.broken = False
                entry.ever_connected = True
                orphaned = self._entries.get(entry.key) is not entry
        if orphaned:  # waehrend des Verbindens freigegeben
            self._disconnect_entry(entry)
            raise ShareError(ERROR_NOT_CONNECTED)
        logger.info("Netzwerkverbindung hergestellt: %s (Benutzer %s)", entry.root, entry.cred.user)

    def _entry_for(self, target: str) -> _Entry | None:
        return next((e for e in self._entries.values() if target in e.targets), None)

    # --- oeffentlich --------------------------------------------------------------
    def acquire(self, target: str, root: str, cred: NetworkCredential) -> str:
        r"""Verbindet (bzw. nutzt die bestehende Verbindung) fuer ``target``.

        Liefert die Zugriffswurzel ``\\<ip>\share``. Ein Ziel, das bisher eine
        andere Freigabe oder andere Zugangsdaten hielt, gibt diese frei.
        """
        unc = parse_unc(root)
        with self._cond:
            if self._closed:
                raise ShareError(ERROR_NOT_CONNECTED, message=CLOSED_MESSAGE)
        # Genau EINE Aufloesung + Pruefung je Verbindungsaufbau (NET-SEC-08);
        # Schluessel ist die gepruefte Adresse: zwei Namen fuer denselben Server
        # teilen sich die Verbindung, statt sie sich gegenseitig zu trennen.
        address = resolve_for_connect(unc.host)
        access_root = access_root_for(unc, address)
        key, host = _key(access_root), address
        stale: list[_Entry] = []
        with self._cond:
            if self._closed:
                raise ShareError(ERROR_NOT_CONNECTED, message=CLOSED_MESSAGE)
            for entry in list(self._entries.values()):
                if target in entry.targets and (entry.key != key or not entry.cred.same_as(cred)):
                    if self._drop_target_locked(entry, target):
                        stale.append(entry)
            conflict = next(
                (e for e in self._entries.values() if e.host == host and _key(e.cred.user) != _key(cred.user)), None,
            )
            entry = self._entries.get(key)
            mismatch = entry is not None and not entry.cred.same_as(cred)
            if conflict is None and not mismatch:
                if entry is None:
                    entry = _Entry(
                        key=key, root=unc.root, host=host, cred=cred, share=_key(unc.share), access_root=access_root,
                    )
                    self._entries[key] = entry
                entry.names.add(_key(unc.root))
                entry.targets.add(target)
        for old in stale:
            self._disconnect_entry(old)
        if conflict is not None:
            raise ShareError(ERROR_SESSION_CREDENTIAL_CONFLICT)
        if mismatch:
            # Gleicher Benutzer, anderes Passwort, aber von anderen Zielen benutzt.
            raise ShareError(
                ERROR_SESSION_CREDENTIAL_CONFLICT,
                message=f"{MESSAGE_PREFIX}Für diese Freigabe ist schon ein anderes Passwort in Gebrauch – "
                        "für Watch- und Export-Ordner auf demselben Server dieselben Zugangsdaten verwenden.",
            )
        try:
            self._connect_entry(entry)
        except BaseException:
            with self._cond:
                drop = self._drop_target_locked(entry, target)
            if drop:
                self._disconnect_entry(entry)
            raise
        return entry.access_root

    def check_connected(self, target: str) -> str:
        r"""Prueft, ob der Verbindungseintrag des Ziels noch besteht — OHNE neu
        zu verbinden (NET-SEC-02). Fehlt er, gilt die Verbindung als defekt
        (keine neuen Zugriffe) und es wird ``ShareError`` geworfen; der
        Aufrufer beendet seine Zugriffe, gibt frei und verbindet spaeter neu.
        Liefert ``\\<ip>\share``."""
        with self._cond:
            entry = self._entry_for(target)
            if entry is None or not entry.connected or entry.broken:
                raise ShareError(ERROR_NOT_CONNECTED)
            access_root = entry.access_root
        if not is_connected(access_root):
            with self._cond:
                entry.broken = True
            logger.warning("Netzwerkverbindung %s fehlt – Zugriffe werden beendet", entry.root)
            raise ShareError(ERROR_NOT_CONNECTED)
        return access_root

    def open_lease(self, target: str) -> Lease:
        """Zugriff beginnen: haelt die Verbindung von ``target`` bis ``close()``.

        Wirft ``ShareError``, wenn das Ziel keine (intakte) Verbindung hat —
        dann darf der Aufrufer den Pfad nicht anfassen."""
        with self._cond:
            entry = self._entry_for(target)
            if self._closed or entry is None or not entry.connected or entry.broken:
                raise ShareError(ERROR_NOT_CONNECTED)
            entry.leases += 1
            return Lease(self, entry)

    def lease(self, target: str) -> Lease:
        """``with connections.lease(WATCH): ...`` — siehe ``open_lease``."""
        return self.open_lease(target)

    def access_path(self, target: str, path_str: str) -> str:
        r"""``\\host\share\rest`` -> ``\\<ip>\share\rest`` fuer das Ziel. Andere
        Pfade (lokal, Tests) bleiben unveraendert."""
        if not is_unc(path_str):
            return path_str
        unc = parse_unc(path_str)
        with self._cond:
            entry = self._entry_for(target)
            if entry is None or not entry.access_root or _key(unc.root) not in entry.names:
                raise ShareError(ERROR_NOT_CONNECTED)
            return "\\".join([entry.access_root, *unc.rest])

    def holds(self, target: str) -> str | None:
        with self._cond:
            entry = self._entry_for(target)
            return entry.root if entry else None

    def in_use(self, path_str: str) -> bool:
        """True, wenn die Freigabe von einem dauerhaften Ziel (Watch/Export)
        oder einem laufenden Zugriff gehalten wird (nicht von einer Probe)."""
        key = share_key(path_str)
        with self._cond:
            return any(
                key in e.names and (e.leases > 0 or any(not t.startswith("probe:") for t in e.targets))
                for e in self._entries.values()
            ) if key else False

    def release(self, target: str) -> None:
        """Ziel freigeben; getrennt wird nach dem letzten laufenden Zugriff."""
        with self._cond:
            to_close = [e for e in list(self._entries.values()) if target in e.targets]
            stale = [e for e in to_close if self._drop_target_locked(e, target)]
        for entry in stale:
            self._disconnect_entry(entry)

    def shutdown(self, wait: float = _SHUTDOWN_WAIT) -> None:
        """Beim Beenden: keine neuen Verbindungen, auf laufende Zugriffe warten
        (hoechstens ``wait`` Sekunden), dann alles trennen."""
        with self._cond:
            self._closed = True
            if not self._cond.wait_for(lambda: all(e.leases <= 0 for e in self._entries.values()), timeout=wait):
                busy = sum(max(e.leases, 0) for e in self._entries.values())
                logger.warning("%d Zugriff(e) auf Netzwerkordner laufen beim Beenden noch – trenne trotzdem", busy)
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            if entry.connected and entry.access_root:
                disconnect(entry.access_root, True)
        if entries:
            logger.info("%d Netzwerkverbindung(en) beim Beenden getrennt", len(entries))

    @property
    def closed(self) -> bool:
        return self._closed


connections = ShareConnections()


# --- Laufwerksbuchstaben -------------------------------------------------------------

@dataclass(frozen=True)
class DriveInfo:
    """Ergebnis von ``classify_drive``.

    kind: ``local`` (fest/wechselbar), ``subst`` (``target`` = echter Pfad),
    ``network_drive`` (Netzlaufwerk, ``suggestions`` = UNC-Vorschlaege),
    ``missing_drive`` (fuer den Dienst nicht vorhanden), ``unsuitable``
    (CD/RAM-Disk), ``none`` (kein Laufwerkspfad / nicht Windows).
    """

    kind: str
    letter: str = ""
    target: str | None = None
    suggestions: tuple[str, ...] = ()


_DRIVE_RE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")


def _join_suggestion(remote: str, rest: str) -> str:
    remote = remote.replace("/", "\\").rstrip("\\")
    rest = rest.replace("/", "\\").strip("\\")
    return f"{remote}\\{rest}" if rest else remote


def classify_drive(path_str: str) -> DriveInfo:
    """Klassifiziert ``X:\\...`` ohne SMB-Zugriff (nur Kernel + Registry)."""
    match = _DRIVE_RE.match(path_str or "")
    if not match or not supported():  # kein Laufwerkspfad / nicht Windows
        return DriveInfo("none")
    letter = match.group(1).upper()
    rest = match.group(2) or ""
    dtype = _api.drive_type(f"{letter}:\\")
    dos = _api.query_dos_device(f"{letter}:") or ""
    if dos.startswith("\\??\\"):
        target = dos[4:]
        if target.upper().startswith("UNC\\"):  # subst auf eine Freigabe
            remote = "\\\\" + target[4:]
            return DriveInfo("network_drive", letter, suggestions=(_join_suggestion(remote, rest),))
        target_path = _join_suggestion(target, rest)
        if target_path.endswith(":"):
            target_path += "\\"
        return DriveInfo("subst", letter, target=target_path)
    if dtype in (DRIVE_FIXED, DRIVE_REMOVABLE):
        return DriveInfo("local", letter)
    if dtype == DRIVE_REMOTE:
        remote = _api.get_connection(f"{letter}:")
        suggestions = (_join_suggestion(remote, rest),) if remote else ()
        return DriveInfo("network_drive", letter, suggestions=suggestions)
    if dtype in (DRIVE_CDROM, DRIVE_RAMDISK):
        return DriveInfo("unsuitable", letter)
    # DRIVE_NO_ROOT_DIR / UNKNOWN: unter dem Dienst der typische Fall fuer
    # Netzlaufwerke des angemeldeten Benutzers (HKU\<SID>\Network\<X>).
    # SEC-NF-03: nur der an der Konsole angemeldete Benutzer — die Laufwerke
    # anderer Windows-Konten gehen die Weboberflaeche nichts an.
    sid = _api.console_user_sid()
    remote = _api.read_user_remote_path(sid, letter) if sid else None
    if remote:
        return DriveInfo("network_drive", letter, suggestions=(_join_suggestion(remote, rest),))
    return DriveInfo("missing_drive", letter)


def drive_message(info: DriveInfo) -> str:
    """Deutsche Meldung fuer abgelehnte Laufwerksbuchstaben."""
    if info.kind == "network_drive":
        text = (
            f"Laufwerk {info.letter}: ist ein Netzlaufwerk. Der Hintergrunddienst sieht verbundene "
            "Netzlaufwerke nicht – bitte den Netzwerkpfad (z.B. \\\\nas\\scans) mit Benutzer und "
            "Passwort der Freigabe angeben."
        )
        if info.suggestions:
            text += " Vorschlag: " + " oder ".join(info.suggestions)
        return text
    if info.kind == "unsuitable":
        return f"Laufwerk {info.letter}: ist ein CD-/RAM-Laufwerk und kein dauerhafter Ordner."
    return (
        f"Laufwerk {info.letter}: ist für den Dienst nicht vorhanden (nur in Ihrer Anmeldung verbunden "
        "oder per subst angelegt) – bitte den echten Pfad angeben."
    )


# --- Verknuepfungen auf Netzwerkpfade (NET-SEC-07) ------------------------------------

# IO_REPARSE_TAG_MOUNT_POINT (Junction) und IO_REPARSE_TAG_SYMLINK
_REDIRECTING_REPARSE_TAGS = frozenset({0xA0000003, 0xA000000C})
_VOLUME_TARGET_RE = re.compile(r"^(\\\\\?\\|\\\?\?\\)?Volume\{[0-9A-Fa-f-]+\}", re.IGNORECASE)
_LINK_DEPTH_MAX = 8


def _link_target(path_str: str) -> str | None:
    """Ziel eines Symlinks/einer Junction (ohne ihm zu folgen), sonst None."""
    st = os.lstat(path_str)
    if not (stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) in _REDIRECTING_REPARSE_TAGS):
        return None
    return os.readlink(path_str)


def _normalize_link_target(target: str) -> str:
    r"""``\??\UNC\srv\x`` / ``\\?\UNC\srv\x`` -> ``\\srv\x``; ``\??\C:\x`` -> ``C:\x``."""
    upper = target[:8].upper()
    for prefix in ("\\??\\UNC\\", "\\\\?\\UNC\\"):
        if upper.startswith(prefix):
            return "\\\\" + target[len(prefix):]
    if target.startswith(("\\??\\", "\\\\?\\")):
        return target[4:]
    return target


def find_remote_link(path_str: str, _depth: int = 0) -> str | None:
    r"""Prueft einen LOKALEN Pfad Komponente fuer Komponente, ohne einer
    Verknuepfung zu folgen: liefert die Komponente, deren Symlink/Junction
    (auch ueber eine Kette lokaler Verknuepfungen) auf einen Netzwerkpfad
    zeigt — sonst None.

    ``realpath``/``resolve`` wuerde das Ziel schon OEFFNEN, und der Dienst
    (LocalSystem) meldete sich mit dem Computerkonto am fremden Server an.
    Nicht vorhandene Komponenten koennen keine Verknuepfungen sein.
    """
    if not path_str or is_unc(path_str):
        return None
    if _depth > _LINK_DEPTH_MAX:
        return path_str
    parts = Path(path_str).parts
    if not parts:
        return None
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        try:
            target = _link_target(str(current))
        except OSError:
            return None  # existiert nicht (bzw. nicht lesbar): dahinter folgt nichts
        if target is None:
            continue
        normalized = _normalize_link_target(target)
        if is_unc(normalized):
            return str(current)
        if _VOLUME_TARGET_RE.match(target) or _VOLUME_TARGET_RE.match(normalized):
            continue  # Laufwerks-Bereitstellungspunkt (lokal)
        nested = Path(normalized)
        if not nested.is_absolute():
            nested = current.parent / nested
        if find_remote_link(str(nested), _depth + 1):
            return str(current)
    return None


def remote_link_message(link: str) -> str:
    return (
        f"Der Ordner führt über eine Verknüpfung ({link}) auf einen Netzwerkpfad – "
        "bitte den Netzwerkpfad direkt mit Benutzer und Passwort der Freigabe angeben."
    )


# --- Lokale Scanner-Freigabe (B) ----------------------------------------------------

def scanner_share_info(name: str) -> tuple[bool, str | None]:
    """(existiert, lokaler Pfad) der SMB-Freigabe ``name`` auf diesem Rechner."""
    if not name:
        return (False, None)
    try:
        return _api.net_share_get_info(name)
    except OSError:
        return (False, None)


def scanner_share_registry() -> dict[str, str]:
    """Vom Setup/Hilfsskript geschriebene Werte (HKLM\\SOFTWARE\\Zettelwirtschaft)."""
    try:
        return _api.read_scanner_registry()
    except OSError:
        return {}
