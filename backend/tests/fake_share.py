r"""Gefaelschte Windows-Netzwerk-API fuer Tests (kein echtes SMB, kein WNet).

``conftest.py`` installiert sie fuer JEDEN Test (autouse) als
``app.core.network_share._api`` — kein Test darf versehentlich eine echte
Verbindung, Freigabe oder Registry-Abfrage dieses Rechners ausloesen.

Freigaben werden auf lokale Ordner abgebildet (``shares``:
``\\nas\scans`` -> ``tmp_path/...``); ``translate()`` uebersetzt UNC-Pfade fuer
die Tests, die echtes Dateisystemverhalten brauchen.

Verbunden wird ueber die gepruefte Adresse (``\\<ip>\share``, NET-SEC-08);
``passwords`` darf trotzdem mit dem Namen (``\\nas\scans``) befuellt werden —
die Adresse wird ueber ``resolve_map`` zurueckuebersetzt.
"""

from __future__ import annotations

import os

from app.core import network_share as ns


class FakeShareApi(ns._UnsupportedApi):
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.connected: dict[str, str] = {}   # casefold(root) -> user
        self.passwords: dict[str, str] = {}   # casefold(root) -> erwartetes Passwort (optional)
        self.add_result: int | None = None    # erzwungener Fehlercode fuer add()
        self.provider_text = ""
        self.drive_types: dict[str, int] = {}
        self.dos_devices: dict[str, str] = {}
        self.remote_drives: dict[str, str] = {}
        self.shares: dict[str, tuple[bool, str | None]] = {}
        self.registry: dict[str, str] = {}
        self.resolve_map: dict[str, list[str]] = {}
        self.dfs_flags: dict[str, int] = {}   # casefold(share) -> shi1005_flags
        self.console_sid: str | None = "S-1-5-21-1-2-3-1001"
        self.user_drives: dict[tuple[str, str], str] = {}  # (sid, Buchstabe) -> RemotePath
        self.add_hook = None                  # optional: Aufruf vor jedem add() (Tests)

    def _name_roots(self, remote: str) -> list[str]:
        r"""``\\<ip>\share`` -> zusaetzlich ``\\<name>\share`` fuer alle Namen mit dieser Adresse."""
        roots = [remote.casefold()]
        parts = remote.strip("\\").split("\\")
        if len(parts) >= 2:
            for name, addrs in self.resolve_map.items():
                if parts[0] in addrs:
                    roots.append(f"\\\\{name}\\{parts[1]}".casefold())
        return roots

    # --- WNet ---------------------------------------------------------------
    def add(self, remote: str, user: str, password: str) -> int:
        # Passwort NICHT protokollieren (Sentinel-Tests durchsuchen calls/caplog)
        self.calls.append(("add", remote, user))
        if self.add_hook is not None:
            self.add_hook(remote)
        if self.add_result is not None:
            return self.add_result
        expected = next((self.passwords[r] for r in self._name_roots(remote) if r in self.passwords), None)
        if expected is not None and expected != password:
            return ns.ERROR_LOGON_FAILURE
        self.connected[remote.casefold()] = user
        return ns.NO_ERROR

    def cancel(self, remote: str, force: bool = False) -> int:
        self.calls.append(("cancel", remote) if not force else ("cancel", remote, "force"))
        if self.connected.pop(remote.casefold(), None) is None:
            return ns.ERROR_NOT_CONNECTED
        return ns.NO_ERROR

    def share_flags(self, server: str, share: str) -> int | None:
        return self.dfs_flags.get(share.casefold(), 0)

    def last_error_text(self) -> str:
        return self.provider_text

    def connected_remotes(self) -> set[str]:
        return set(self.connected)

    # --- Laufwerke ------------------------------------------------------------
    def get_connection(self, local: str) -> str | None:
        return self.remote_drives.get(local.upper())

    def drive_type(self, root: str) -> int:
        letter = root[0].upper()
        if letter in self.drive_types:
            return self.drive_types[letter]
        return ns.DRIVE_FIXED if os.path.exists(root) else ns.DRIVE_NO_ROOT_DIR

    def query_dos_device(self, device: str) -> str | None:
        return self.dos_devices.get(device.upper(), "\\Device\\HarddiskVolume1")

    def console_user_sid(self) -> str | None:
        return self.console_sid

    def read_user_remote_path(self, sid: str, letter: str) -> str | None:
        return self.user_drives.get((sid, letter.upper()))

    # --- Freigaben dieses Rechners ----------------------------------------------
    def net_share_get_info(self, name: str) -> tuple[bool, str | None]:
        return self.shares.get(name.casefold(), (False, None))

    def read_scanner_registry(self) -> dict[str, str]:
        return dict(self.registry)
