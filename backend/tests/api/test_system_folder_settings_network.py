r"""Netzwerkordner (UNC + Zugangsdaten) in den Ordner-Einstellungen.

Kein echtes SMB: die Windows-API ist gefaelscht (conftest ``fake_share_api``),
Freigaben werden ueber ``system._fs_path`` auf lokale Ordner abgebildet.
Sicherheitsregeln aus dem Audit (FH-01/INJ-03/INFRA-02, SEC-DATA-02) und der
Sentinel-Test fuer das Passwort stehen hier.
"""

import asyncio
import json
import logging
import os
import socket
import threading

import pytest

import app.config
from app.api import system as system_api
from app.config import get_settings
from app.core import network_share as ns
from app.main import app as fastapi_app
from app.services import network_folder_service as nfs
from app.services.crypto_service import decrypt_password
from app.services.settings_service import get_db_setting, set_db_setting
from tests.credentials import (
    TEST_EMAIL_ENCRYPTION_KEY,
    TEST_SMB_PASSWORD,
    TEST_SMB_PASSWORD_2,
    TEST_SMB_USER,
)

BS = "\\"


def unc(*parts: str) -> str:
    return BS * 2 + BS.join(parts)


WATCH_UNC = unc("nas", "scans", "eingang")
EXPORT_UNC = unc("nas", "scans", "fertig")


@pytest.fixture(autouse=True)
def dev_mode(monkeypatch):
    monkeypatch.setattr(app.config, "_in_container", lambda: False)
    monkeypatch.setattr(app.config, "_is_frozen", lambda: False)


@pytest.fixture
def key_settings(test_settings):
    s = test_settings.model_copy(update={"EMAIL_ENCRYPTION_KEY": TEST_EMAIL_ENCRYPTION_KEY})
    fastapi_app.dependency_overrides[get_settings] = lambda: s
    yield s
    fastapi_app.dependency_overrides[get_settings] = lambda: test_settings


@pytest.fixture
def restarts(monkeypatch):
    calls = []

    async def fake_restart(app, settings):
        calls.append(settings)

    monkeypatch.setattr(system_api, "_restart_watch_task", fake_restart)
    return calls


@pytest.fixture
def shares(tmp_path, monkeypatch, fake_share_api):
    r"""``\\nas\scans`` und ``\\nas2\daten`` als lokale Ordner; NAS-Passwort fest."""
    roots = {}
    for root in (unc("nas", "scans"), unc("nas2", "daten"), unc("evil", "share")):
        local = tmp_path / ("share_" + root.strip(BS).replace(BS, "_"))
        local.mkdir()
        roots[root.casefold()] = local
    fake_share_api.passwords[unc("nas", "scans").casefold()] = TEST_SMB_PASSWORD
    for host, addr in (("nas", "198.51.100.20"), ("nas2", "198.51.100.21"), ("evil", "198.51.100.66")):
        fake_share_api.resolve_map[host] = [addr]
        # Zugriffe laufen ueber die gepruefte Adresse (\\<ip>\share, NET-SEC-08)
        for root, local in list(roots.items()):
            name_host, share = root.strip(BS).split(BS)[:2]
            if name_host == host:
                roots[unc(addr, share).casefold()] = local
    accessed: list[str] = []

    def fs_path(p: str) -> str:
        accessed.append(p)
        for root, local in roots.items():
            if p.casefold() == root or p.casefold().startswith(root + BS):
                rest = p[len(root):].strip(BS)
                return str(local / rest.replace(BS, os.sep)) if rest else str(local)
        raise AssertionError(f"Unerwarteter Zugriff auf {p}")

    monkeypatch.setattr(system_api, "_fs_path", fs_path)
    fake_share_api.fs_accessed = accessed
    return roots


def _body(**kw):
    body = {"watch_dir": "", "export_dir": ""}
    body.update(kw)
    return body


def _cred_body(path=WATCH_UNC, user=TEST_SMB_USER, password=TEST_SMB_PASSWORD, target="watch", **extra):
    return _body(**{f"{target}_dir": path, f"{target}_dir_net_user": user, f"{target}_dir_net_password": password}, **extra)


@pytest.mark.asyncio
class TestSaveNetworkFolder:
    async def test_unc_with_credentials_is_saved_encrypted(
        self, client, key_settings, shares, restarts, fake_share_api, test_session_factory,
    ):
        resp = await client.put("/api/system/settings", json=_cred_body(watch_poll_interval=45))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["watch_dir"] == WATCH_UNC
        assert data["watch_dir_net_user"] == TEST_SMB_USER
        assert data["watch_dir_net_password_set"] is True
        assert data["watch_poll_interval"] == 45
        assert data["network_folders_supported"] is True
        assert not [k for k in data if "password" in k and not k.endswith("_password_set")]
        assert TEST_SMB_PASSWORD not in resp.text
        # Ordner wurde nach dem Verbinden auf der "Freigabe" angelegt
        assert (shares[unc("nas", "scans").casefold()] / "eingang").is_dir()
        # Test-Verbindung wieder getrennt (der Watch-Task verbindet sich selbst)
        assert fake_share_api.connected == {}
        assert len(restarts) == 1
        async with test_session_factory() as s:
            token = await get_db_setting(s, "watch_dir_net_password")
        assert token and TEST_SMB_PASSWORD not in token
        assert decrypt_password(token, TEST_EMAIL_ENCRYPTION_KEY) == TEST_SMB_PASSWORD

    async def test_unc_without_credentials_rejected(self, client, key_settings, shares, restarts, fake_share_api):
        for target in ("watch", "export"):
            resp = await client.put("/api/system/settings", json=_body(**{f"{target}_dir": WATCH_UNC}))
            assert resp.status_code == 400
            assert "Netzwerkpfade" in resp.json()["detail"]
        assert fake_share_api.calls == []

    async def test_user_without_password_rejected(self, client, key_settings, shares, restarts):
        resp = await client.put("/api/system/settings", json=_cred_body(password=None))
        assert resp.status_code == 400
        assert "Benutzer und Passwort" in resp.json()["detail"]

    @pytest.mark.parametrize("bad", [
        BS * 2 + "?" + BS + "UNC" + BS + "nas" + BS + "scans",
        unc("nas", "C$", "x"),
        unc("nas", "IPC$"),
        unc("localhost", "scans"),
        unc("127.0.0.1", "scans"),
        unc("nas@SSL@443", "scans"),
        unc("nas", "DavWWWRoot", "scans"),
        unc("[fe80::1]", "scans"),
        unc("alias", "scans"),  # DNS-Alias auf diesen Rechner (unten)
        unc("eigen", "scans"),  # eigene IP (unten)
    ])
    async def test_dangerous_forms_rejected_without_connecting(
        self, client, key_settings, shares, restarts, fake_share_api, bad,
    ):
        fake_share_api.resolve_map["alias"] = ["127.0.0.2"]
        fake_share_api.resolve_map[socket.gethostname().lower()] = ["198.51.100.99"]
        fake_share_api.resolve_map["eigen"] = ["198.51.100.99"]
        ns._reset_caches()
        for target in ("watch", "export"):
            resp = await client.put("/api/system/settings", json=_cred_body(path=bad, target=target))
            assert resp.status_code == 400, (target, bad, resp.text)
        assert not [c for c in fake_share_api.calls if c[0] == "add"]
        assert restarts == []

    async def test_no_filesystem_access_before_connect(
        self, client, key_settings, shares, restarts, fake_share_api, monkeypatch,
    ):
        """FH-01: Ohne erfolgreiche Verbindung kein realpath()/stat() auf den UNC-Pfad."""
        touched = []
        real = system_api.os.path.realpath
        monkeypatch.setattr(system_api.os.path, "realpath", lambda p, *a, **k: touched.append(str(p)) or real(p, *a, **k))
        fake_share_api.add_result = ns.ERROR_LOGON_FAILURE
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 400
        assert "Anmeldung fehlgeschlagen" in resp.json()["detail"]
        assert touched == []
        data = (await client.get("/api/system/settings")).json()
        assert data["watch_dir"] != WATCH_UNC
        assert data["watch_dir_net_password_set"] is False

    @pytest.mark.parametrize("code, fragment", [
        (ns.ERROR_LOGON_FAILURE, "Benutzername oder Passwort falsch"),
        (ns.ERROR_BAD_NETPATH, "Server nicht gefunden"),
        (ns.ERROR_BAD_NET_NAME, "Freigabe gibt es auf dem Server nicht"),
    ])
    async def test_connect_errors_are_german_and_save_nothing(
        self, client, key_settings, shares, restarts, fake_share_api, code, fragment,
    ):
        fake_share_api.add_result = code
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 400
        assert fragment in resp.json()["detail"]
        # Watch-Task wurde vor dem Test gestoppt und mit den alten Einstellungen neu gestartet
        assert len(restarts) == 1
        assert (await client.get("/api/system/settings")).json()["watch_dir"] != WATCH_UNC

    async def test_dfs_share_rejected_before_any_path_access(
        self, client, key_settings, shares, restarts, fake_share_api,
    ):
        """NET-SEC-09: DFS wird ueber die Freigabe-Flags erkannt — ohne den Pfad
        anzufassen (und damit ohne einer Weiterleitung zu folgen)."""
        fake_share_api.dfs_flags["scans"] = ns.SHI1005_FLAGS_DFS_ROOT
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 400
        assert "DFS" in resp.json()["detail"]
        assert fake_share_api.fs_accessed == []
        assert fake_share_api.connected == {}

    async def test_connects_and_accesses_via_checked_address(self, client, key_settings, shares, restarts, fake_share_api):
        """NET-SEC-08: verbunden und zugegriffen wird ueber die gepruefte IPv4-
        Adresse — der Redirector kann den Namen nicht anders aufloesen."""
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 200, resp.text
        adds = [c for c in fake_share_api.calls if c[0] == "add"]
        assert adds == [("add", unc("198.51.100.20", "scans"), TEST_SMB_USER)]
        assert fake_share_api.fs_accessed and all(
            p.casefold().startswith(unc("198.51.100.20", "scans").casefold()) for p in fake_share_api.fs_accessed
        )
        assert resp.json()["watch_dir"] == WATCH_UNC  # gespeichert wird der Name

    async def test_unresolvable_host_fails_closed(self, client, key_settings, shares, restarts, fake_share_api):
        """NET-SEC-08: nicht aufloesbar -> nicht pruefbar -> abgelehnt, ohne Verbindungsversuch."""
        fake_share_api.resolve_map["geist"] = []
        resp = await client.put("/api/system/settings", json=_cred_body(path=unc("geist", "scans", "x")))
        assert resp.status_code == 400
        assert "Server nicht gefunden" in resp.json()["detail"]
        assert not [c for c in fake_share_api.calls if c[0] == "add"]

    @pytest.mark.parametrize("share", ["C$ ", "C$.", "ADMIN$ ."])
    async def test_admin_share_with_trailing_dot_or_space_rejected(
        self, client, key_settings, shares, restarts, fake_share_api, share,
    ):
        """NET-SEC-10: Win32 entfernt Punkt/Leerzeichen am Ende — ``C$ `` waere ``C$``."""
        resp = await client.put("/api/system/settings", json=_cred_body(path=unc("nas", share)))
        assert resp.status_code == 400
        assert not [c for c in fake_share_api.calls if c[0] == "add"]

    async def test_same_host_different_user_rejected(self, client, key_settings, shares, restarts):
        body = _cred_body()
        body |= {"export_dir": unc("nas", "scans", "fertig"), "export_dir_net_user": "anderer",
                 "export_dir_net_password": TEST_SMB_PASSWORD_2}
        resp = await client.put("/api/system/settings", json=body)
        assert resp.status_code == 400
        assert "denselben Benutzer" in resp.json()["detail"]

    async def test_watch_and_export_on_same_share(self, client, key_settings, shares, restarts, fake_share_api):
        body = _cred_body()
        body |= {"export_dir": EXPORT_UNC, "export_dir_net_user": TEST_SMB_USER,
                 "export_dir_net_password": TEST_SMB_PASSWORD}
        resp = await client.put("/api/system/settings", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["export_dir"] == EXPORT_UNC
        assert resp.json()["export_dir_net_password_set"] is True

    async def test_missing_encryption_key_is_503(self, client, shares, restarts):
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 503
        assert "EMAIL_ENCRYPTION_KEY" in resp.json()["detail"]

    async def test_unsupported_platform(self, client, key_settings, shares, restarts, monkeypatch):
        monkeypatch.setattr(ns, "_api", ns._UnsupportedApi())
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 400
        assert "nur unter Windows" in resp.json()["detail"]


@pytest.mark.asyncio
class TestStoredPassword:
    async def _save(self, client):
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 200, resp.text

    async def test_none_keeps_password_for_same_share_and_user(
        self, client, key_settings, shares, restarts, test_session_factory,
    ):
        await self._save(client)
        async with test_session_factory() as s:
            token = await get_db_setting(s, "watch_dir_net_password")
        # anderer Unterordner auf derselben Freigabe: erlaubt, Passwort bleibt
        resp = await client.put("/api/system/settings", json=_cred_body(path=unc("NAS", "Scans", "neu"), password=None))
        assert resp.status_code == 200, resp.text
        async with test_session_factory() as s:
            assert await get_db_setting(s, "watch_dir_net_password") == token

    @pytest.mark.parametrize("change", [
        {"path": unc("evil", "share", "x")},       # anderer Server
        {"path": unc("nas", "andere")},            # andere Freigabe
        {"user": "anderer"},                       # anderer Benutzer
    ])
    async def test_retargeting_requires_password_again(
        self, client, key_settings, shares, restarts, fake_share_api, test_session_factory, change,
    ):
        """SEC-DATA-02: Das gespeicherte Passwort darf nicht an einen anderen
        Server/eine andere Freigabe/einen anderen Benutzer gehen."""
        await self._save(client)
        async with test_session_factory() as s:
            token = await get_db_setting(s, "watch_dir_net_password")
        adds = len([c for c in fake_share_api.calls if c[0] == "add"])
        resp = await client.put("/api/system/settings", json=_cred_body(password=None, **change))
        assert resp.status_code == 400
        assert "erneut eingeben" in resp.json()["detail"]
        test = await client.post("/api/system/settings/test-folder", json={
            "target": "watch", "path": change.get("path", WATCH_UNC), "net_user": change.get("user", TEST_SMB_USER),
        })
        assert test.json()["ok"] is False
        assert "erneut eingeben" in test.json()["message"]
        assert len([c for c in fake_share_api.calls if c[0] == "add"]) == adds  # kein Verbindungsversuch
        async with test_session_factory() as s:
            assert await get_db_setting(s, "watch_dir_net_password") == token

    async def test_switch_to_local_clears_credentials(
        self, client, key_settings, shares, restarts, tmp_path, test_session_factory,
    ):
        await self._save(client)
        resp = await client.put("/api/system/settings", json=_body(watch_dir=str(tmp_path / "lokal")))
        assert resp.status_code == 200, resp.text
        assert resp.json()["watch_dir_net_password_set"] is False
        async with test_session_factory() as s:
            assert await get_db_setting(s, "watch_dir_net_password") == ""
            assert await get_db_setting(s, "watch_dir_net_user") == ""

    async def test_empty_password_deletes(self, client, key_settings, shares, restarts):
        await self._save(client)
        resp = await client.put("/api/system/settings", json=_cred_body(password=""))
        assert resp.status_code == 400  # UNC ohne Passwort ist nicht erlaubt
        assert (await client.get("/api/system/settings")).json()["watch_dir_net_password_set"] is True

    async def test_wrong_new_password_keeps_old_settings(self, client, key_settings, shares, restarts):
        await self._save(client)
        resp = await client.put("/api/system/settings", json=_cred_body(password=TEST_SMB_PASSWORD_2))
        assert resp.status_code == 400
        assert "Passwort falsch" in resp.json()["detail"]
        # Watch-Task lief mit den alten Einstellungen weiter (gestoppt -> neu gestartet)
        assert len(restarts) == 2

    async def test_unchanged_settings_do_not_restart(self, client, key_settings, shares, restarts):
        await self._save(client)
        resp = await client.put("/api/system/settings", json=_cred_body(password=None))
        assert resp.status_code == 200, resp.text
        assert len(restarts) == 1

    async def test_interval_change_restarts_watch(self, client, key_settings, shares, restarts):
        await self._save(client)
        resp = await client.put("/api/system/settings", json=_cred_body(password=None, watch_poll_interval=120))
        assert resp.status_code == 200
        assert resp.json()["watch_poll_interval"] == 120
        assert len(restarts) == 2

    async def test_interval_bounds(self, client, key_settings, shares, restarts):
        for bad in (5, 601):
            resp = await client.put("/api/system/settings", json=_body(watch_poll_interval=bad))
            assert resp.status_code == 422

    async def test_export_path_change_releases_connection(
        self, client, key_settings, shares, restarts, fake_share_api, tmp_path,
    ):
        resp = await client.put("/api/system/settings", json=_cred_body(path=EXPORT_UNC, target="export"))
        assert resp.status_code == 200, resp.text
        # Export haelt die Verbindung (wie nach der ersten Export-Kopie)
        ns.connections.acquire(nfs.EXPORT, unc("nas", "scans"), ns.NetworkCredential(TEST_SMB_USER, TEST_SMB_PASSWORD))
        resp = await client.put("/api/system/settings", json=_body(export_dir=str(tmp_path / "lokal-export")))
        assert resp.status_code == 200, resp.text
        assert ns.connections.holds(nfs.EXPORT) is None
        assert fake_share_api.connected == {}


@pytest.mark.asyncio
class TestDriveLetters:
    async def test_network_drive_rejected_with_suggestion(self, client, key_settings, restarts, fake_share_api):
        fake_share_api.drive_types["V"] = ns.DRIVE_NO_ROOT_DIR
        fake_share_api.user_drives[(fake_share_api.console_sid, "V")] = unc("nas", "scans")
        resp = await client.put("/api/system/settings", json=_body(watch_dir=r"V:\eingang"))
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Netzlaufwerk" in detail and unc("nas", "scans", "eingang") in detail
        test = (await client.post("/api/system/settings/test-folder", json={"target": "watch", "path": r"V:\eingang"})).json()
        assert test["ok"] is False
        assert test["kind"] == "network_drive"
        assert test["suggested_paths"] == [unc("nas", "scans", "eingang")]
        assert fake_share_api.calls == []

    @pytest.mark.skipif(os.name != "nt", reason="Laufwerksbuchstaben nur unter Windows")
    async def test_subst_drive_is_stored_as_real_target(self, client, key_settings, restarts, fake_share_api, tmp_path):
        real = tmp_path / "echt"
        fake_share_api.drive_types["S"] = ns.DRIVE_FIXED
        fake_share_api.dos_devices["S:"] = "\\??\\" + str(real)
        resp = await client.put("/api/system/settings", json=_body(watch_dir=r"S:\eingang"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["watch_dir"] == str(real / "eingang")
        assert (real / "eingang").is_dir()

    async def test_other_users_mapped_drives_are_not_disclosed(self, client, key_settings, restarts, fake_share_api):
        """SEC-NF-03: Vorschlaege nur aus der Anmeldung des Konsolenbenutzers —
        die Netzlaufwerke anderer Windows-Konten bleiben unsichtbar."""
        fake_share_api.drive_types["Z"] = ns.DRIVE_NO_ROOT_DIR
        fake_share_api.user_drives[("S-1-5-21-9-9-9-1002", "Z")] = unc("fremd", "privat")
        test = (await client.post("/api/system/settings/test-folder", json={"target": "watch", "path": "Z:\\"})).json()
        assert test["ok"] is False
        assert test["suggested_paths"] == []
        assert "fremd" not in json.dumps(test)
        resp = await client.put("/api/system/settings", json=_body(watch_dir="Z:\\x"))
        assert resp.status_code == 400 and "fremd" not in resp.text

    async def test_missing_drive_rejected(self, client, key_settings, restarts, fake_share_api):
        fake_share_api.drive_types["Q"] = ns.DRIVE_NO_ROOT_DIR
        resp = await client.put("/api/system/settings", json=_body(export_dir=r"Q:\x"))
        assert resp.status_code == 400
        assert "nicht vorhanden" in resp.json()["detail"]


@pytest.mark.asyncio
class TestTestFolder:
    async def test_unc_success_and_disconnect(self, client, key_settings, shares, fake_share_api):
        (shares[unc("nas", "scans").casefold()] / "eingang").mkdir()
        resp = await client.post("/api/system/settings/test-folder", json={
            "target": "watch", "path": WATCH_UNC, "net_user": TEST_SMB_USER, "net_password": TEST_SMB_PASSWORD,
        })
        data = resp.json()
        assert data == {"ok": True, "kind": "unc", "message": "Verbindung erfolgreich – Ordner lesbar und beschreibbar.",
                        "suggested_paths": []}
        assert fake_share_api.connected == {}
        assert not list((shares[unc("nas", "scans").casefold()] / "eingang").iterdir())  # Testdatei entfernt

    async def test_missing_folder_is_not_created(self, client, key_settings, shares):
        data = (await client.post("/api/system/settings/test-folder", json={
            "target": "export", "path": EXPORT_UNC, "net_user": TEST_SMB_USER, "net_password": TEST_SMB_PASSWORD,
        })).json()
        assert data["ok"] is True
        assert "wird beim Speichern angelegt" in data["message"]
        assert not (shares[unc("nas", "scans").casefold()] / "fertig").exists()

    async def test_wrong_password(self, client, key_settings, shares):
        data = (await client.post("/api/system/settings/test-folder", json={
            "target": "watch", "path": WATCH_UNC, "net_user": TEST_SMB_USER, "net_password": TEST_SMB_PASSWORD_2,
        })).json()
        assert data["ok"] is False
        assert "Anmeldung fehlgeschlagen" in data["message"]

    async def test_uses_stored_password_for_same_share(self, client, key_settings, shares, restarts):
        await client.put("/api/system/settings", json=_cred_body())
        data = (await client.post("/api/system/settings/test-folder", json={
            "target": "watch", "path": WATCH_UNC, "net_user": TEST_SMB_USER,
        })).json()
        assert data["ok"] is True, data

    async def test_local_folder(self, client, key_settings, tmp_path):
        data = (await client.post("/api/system/settings/test-folder", json={
            "target": "watch", "path": str(tmp_path / "neu"),
        })).json()
        assert data["ok"] is True and data["kind"] == "local"
        bad = (await client.post("/api/system/settings/test-folder", json={"target": "watch", "path": "relativ"})).json()
        assert bad["ok"] is False

    async def test_rate_limited(self, client, key_settings, tmp_path):
        for _ in range(10):
            resp = await client.post("/api/system/settings/test-folder", json={"target": "watch", "path": str(tmp_path)})
            assert resp.status_code == 200
        resp = await client.post("/api/system/settings/test-folder", json={"target": "watch", "path": str(tmp_path)})
        assert resp.status_code == 429

    async def test_put_with_network_path_is_rate_limited(self, client, key_settings, shares, restarts, fake_share_api):
        fake_share_api.add_result = ns.ERROR_LOGON_FAILURE
        codes = [(await client.put("/api/system/settings", json=_cred_body())).status_code for _ in range(11)]
        assert codes[:10] == [400] * 10
        assert codes[10] == 429

    async def test_rate_limit_applies_before_name_resolution(
        self, client, key_settings, shares, restarts, fake_share_api, monkeypatch,
    ):
        """SEC-NF-02: das Netzwerkordner-Limit greift VOR jeder Namensaufloesung,
        und die Aufloesung laeuft nie im Event-Loop-Thread."""
        loop_thread = threading.get_ident()
        lookups: list[tuple[str, int]] = []
        real = ns._resolve_addresses

        def counting(host):
            lookups.append((host, threading.get_ident()))
            return real(host)

        monkeypatch.setattr(ns, "_resolve_addresses", counting)
        fake_share_api.resolve_map["unbekannt"] = []  # z.B. LLMNR/NetBIOS ohne Antwort
        body = _cred_body(path=unc("unbekannt", "x", "y"))
        for _ in range(10):
            await client.put("/api/system/settings", json=body)
        assert lookups and all(tid != loop_thread for _, tid in lookups)
        before = len(lookups)
        resp = await client.put("/api/system/settings", json=body)
        assert resp.status_code == 429
        assert len(lookups) == before  # abgewiesen, bevor aufgeloest wurde

    async def test_docker_not_applicable(self, client, monkeypatch):
        monkeypatch.setattr(app.config, "_in_container", lambda: True)
        data = (await client.post("/api/system/settings/test-folder", json={"target": "watch", "path": WATCH_UNC})).json()
        assert data["ok"] is False and data["kind"] == "unsupported"


@pytest.mark.asyncio
class TestNoStoredPasswordOracle:
    """SEC-NF-01: Das gespeicherte NAS-Passwort laesst sich nicht ueber einen
    Vergleich mit einer Eingabe erraten (ohne Anmeldung an der NAS)."""

    async def _save_watch(self, client):
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 200, resp.text

    def _guess_body(self, guess):
        return {
            "watch_dir": WATCH_UNC, "watch_dir_net_user": TEST_SMB_USER, "watch_dir_net_password": None,
            "export_dir": EXPORT_UNC, "export_dir_net_user": TEST_SMB_USER, "export_dir_net_password": guess,
        }

    async def test_guess_is_never_compared_with_stored_password(
        self, client, key_settings, shares, restarts, fake_share_api,
    ):
        await self._save_watch(client)
        adds = len([c for c in fake_share_api.calls if c[0] == "add"])
        wrong = await client.put("/api/system/settings", json=self._guess_body(TEST_SMB_PASSWORD_2))
        # Kein "anderer Benutzer/anderes Passwort" ohne NAS-Anmeldung: die
        # Eingabe wird an der NAS geprueft (und von deren Sperre erfasst).
        assert wrong.status_code == 400
        assert "anderen Benutzer" not in wrong.json()["detail"]
        assert "Anmeldung fehlgeschlagen" in wrong.json()["detail"]
        assert len([c for c in fake_share_api.calls if c[0] == "add"]) > adds
        right = await client.put("/api/system/settings", json=self._guess_body(TEST_SMB_PASSWORD))
        assert right.status_code == 200, right.text

    async def test_guessing_counts_against_network_folder_limit(
        self, client, key_settings, shares, restarts, fake_share_api,
    ):
        await self._save_watch(client)  # 1. Anfrage
        codes = [
            (await client.put("/api/system/settings", json=self._guess_body(TEST_SMB_PASSWORD_2))).status_code
            for _ in range(10)
        ]
        assert codes[-1] == 429

    async def test_test_folder_does_not_compare_with_held_connection(
        self, client, key_settings, shares, restarts, fake_share_api,
    ):
        await self._save_watch(client)
        # Der Watch-Task haelt die Verbindung mit dem gespeicherten Passwort.
        ns.connections.acquire(nfs.WATCH, unc("nas", "scans"), ns.NetworkCredential(TEST_SMB_USER, TEST_SMB_PASSWORD))
        results = []
        for guess in (TEST_SMB_PASSWORD_2, TEST_SMB_PASSWORD):
            results.append((await client.post("/api/system/settings/test-folder", json={
                "target": "export", "path": EXPORT_UNC, "net_user": TEST_SMB_USER, "net_password": guess,
            })).json())
        assert results[0] == results[1]
        assert "nicht getestet" in results[0]["message"]


@pytest.mark.asyncio
class TestNoUncAccessWithoutConnection:
    async def test_scanner_share_status_never_resolves_unc_watch_dir(
        self, client, key_settings, shares, restarts, fake_share_api, tmp_path, monkeypatch, test_session_factory,
    ):
        """NET-SEC-01: GET/PUT mit Scanner-Freigabe (B) und UNC-Watch-Ordner (A):
        realpath (CreateFileW) bekommt nie den UNC-Pfad."""
        scan = tmp_path / "scan"
        scan.mkdir()
        fake_share_api.registry = {"ScannerShareName": "Zettelwirtschaft-Eingang", "ScannerSharePath": str(scan)}
        fake_share_api.shares["zettelwirtschaft-eingang"] = (True, str(scan))
        touched = []
        real = system_api.os.path.realpath
        monkeypatch.setattr(system_api.os.path, "realpath", lambda p, *a, **k: touched.append(str(p)) or real(p, *a, **k))
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 200, resp.text
        assert resp.json()["scanner_share"]["matches_watch_dir"] is False
        data = (await client.get("/api/system/settings")).json()
        assert data["scanner_share"]["exists"] is True
        assert not [p for p in touched if p.replace("/", BS).startswith(BS * 2)]

    async def test_unc_default_from_config_is_not_accepted_unchecked(
        self, client, test_settings, restarts, fake_share_api, monkeypatch,
    ):
        """NET-SEC-06: ein UNC-Standard aus config.toml wird beim Speichern
        geprueft (ohne Zugangsdaten abgelehnt) statt ungeprueft angelegt."""
        s = test_settings.model_copy(update={"WATCH_DIR": WATCH_UNC, "EMAIL_ENCRYPTION_KEY": TEST_EMAIL_ENCRYPTION_KEY})
        fastapi_app.dependency_overrides[get_settings] = lambda: s
        made = []
        monkeypatch.setattr(system_api.Path, "mkdir", lambda self, *a, **k: made.append(str(self)))
        try:
            resp = await client.put("/api/system/settings", json=_body(watch_dir=WATCH_UNC))
            assert resp.status_code == 400
            assert "Netzwerkpfade" in resp.json()["detail"]
            resp = await client.put("/api/system/settings", json=_body())
            assert resp.status_code == 400
        finally:
            fastapi_app.dependency_overrides[get_settings] = lambda: test_settings
        assert not [p for p in made if p.startswith(BS * 2)]

    async def test_local_path_linking_to_share_rejected_before_resolving(
        self, client, key_settings, restarts, fake_share_api, tmp_path, monkeypatch,
    ):
        """NET-SEC-07: Verknuepfung auf eine Freigabe wird ohne ihr zu folgen
        erkannt — realpath wird gar nicht erst aufgerufen."""
        link = tmp_path / "scans"
        link.mkdir()

        def fake_target(p):
            return unc("angreifer", "x") if os.path.normcase(p) == os.path.normcase(str(link)) else None

        monkeypatch.setattr(ns, "_link_target", fake_target)
        touched = []
        real = system_api.os.path.realpath
        monkeypatch.setattr(system_api.os.path, "realpath", lambda p, *a, **k: touched.append(str(p)) or real(p, *a, **k))
        resp = await client.put("/api/system/settings", json=_body(watch_dir=str(link / "eingang")))
        assert resp.status_code == 400
        assert "Verknüpfung" in resp.json()["detail"]
        assert not [p for p in touched if str(link) in p]


@pytest.mark.asyncio
class TestPasswordNeverLeaks:
    async def test_sentinel(self, client, key_settings, shares, restarts, fake_share_api, caplog):
        """Das Passwort taucht in keinem Log, keiner Antwort und keinem Status auf."""
        caplog.set_level(logging.DEBUG)
        outputs = []
        outputs.append((await client.put("/api/system/settings", json=_cred_body())).text)
        outputs.append((await client.get("/api/system/settings")).text)
        outputs.append((await client.post("/api/system/settings/test-folder", json={
            "target": "watch", "path": WATCH_UNC, "net_user": TEST_SMB_USER, "net_password": TEST_SMB_PASSWORD,
        })).text)
        fake_share_api.add_result = 1208
        fake_share_api.provider_text = f"Fehler mit {TEST_SMB_PASSWORD}"
        outputs.append((await client.post("/api/system/settings/test-folder", json={
            "target": "watch", "path": WATCH_UNC, "net_user": TEST_SMB_USER, "net_password": TEST_SMB_PASSWORD,
        })).text)
        outputs.append((await client.put("/api/system/settings", json=_cred_body())).text)
        nfs.status.update(nfs.WATCH, mode="netzwerk", state="getrennt", path=WATCH_UNC, user=TEST_SMB_USER,
                          reason=ns.message_for(1208, "x"))
        outputs.append((await client.get("/api/system/health")).text)
        for text in outputs + [caplog.text, repr(fake_share_api.calls), json.dumps(nfs.status.get(nfs.WATCH))]:
            assert TEST_SMB_PASSWORD not in text



@pytest.mark.asyncio
class TestHealthAndScannerShare:
    async def test_health_shows_unreachable_watch_folder(self, client):
        nfs.status.update(nfs.WATCH, mode="netzwerk", state="getrennt", path=WATCH_UNC, user=TEST_SMB_USER,
                          reason=ns.message_for(ns.ERROR_BAD_NETPATH), next_retry_at="2026-10-18T10:00:00+00:00")
        data = (await client.get("/api/system/health")).json()
        wf = data["components"]["watch_folder"]
        assert wf["status"] == "warning"
        assert wf["message"].startswith("nicht erreichbar: Netzwerkordner: Server nicht gefunden")
        assert wf["path"] == WATCH_UNC and wf["user"] == TEST_SMB_USER
        assert wf["next_retry_at"] == "2026-10-18T10:00:00+00:00"
        assert data["status"] in ("warning", "error")

    async def test_health_ok_local(self, client):
        nfs.status.update(nfs.WATCH, mode="lokal", state="ok", path="C:/x", user=None)
        wf = (await client.get("/api/system/health")).json()["components"]["watch_folder"]
        assert wf["status"] == "ok" and wf["message"] == "erreichbar" and wf["last_ok_at"]

    async def test_scanner_share_status(self, client, test_settings, test_session_factory, fake_share_api, tmp_path):
        watch = tmp_path / "eingang"
        watch.mkdir()
        async with test_session_factory() as s:
            await set_db_setting(s, "watch_dir", str(watch))
            await s.commit()
        fake_share_api.registry = {"ScannerShareName": "Zettelwirtschaft-Eingang", "ScannerShareUser": "PC\\zw-scanner",
                                   "ScannerSharePath": str(watch)}
        fake_share_api.shares["zettelwirtschaft-eingang"] = (True, str(watch))
        share = (await client.get("/api/system/settings")).json()["scanner_share"]
        assert share["configured"] is True and share["exists"] is True and share["matches_watch_dir"] is True
        assert share["unc_path"].endswith("\\Zettelwirtschaft-Eingang")
        assert share["user"] == "PC\\zw-scanner"
        assert "password" not in json.dumps(share).lower()

        fake_share_api.shares["zettelwirtschaft-eingang"] = (True, str(tmp_path / "anders"))
        share = (await client.get("/api/system/settings")).json()["scanner_share"]
        assert share["matches_watch_dir"] is False

    async def test_scanner_share_not_configured(self, client):
        share = (await client.get("/api/system/settings")).json()["scanner_share"]
        assert share == {"configured": False, "exists": False, "unc_path": "", "user": "", "path": "",
                         "matches_watch_dir": False}


@pytest.mark.asyncio
class TestDockerUnchanged:
    async def test_docker_has_no_network_credentials(self, client, monkeypatch, test_settings):
        monkeypatch.setattr(app.config, "_in_container", lambda: True)
        data = (await client.get("/api/system/settings")).json()
        assert data["network_folders_supported"] is False
        assert data["scanner_share"] is None
        # Docker: keine eigenen SMB-Verbindungen — Zugangsdaten werden ignoriert,
        # Host-Pfade verhalten sich wie bisher (CIFS-Volume per docker-compose).
        resp = await client.put("/api/system/settings", json=_cred_body(path="", watch_dir_host=r"D:\Docs\watch"))
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["watch_dir"] == "/app/external/watch"
        assert data["watch_dir_net_user"] == "" and data["watch_dir_net_password_set"] is False
        # UNC als Watch-Ordner (nicht Host-Pfad) bleibt abgelehnt, mit CIFS-Hinweis
        resp = await client.put("/api/system/settings", json=_cred_body())
        assert resp.status_code == 400
        assert "CIFS" in resp.json()["detail"]


def test_canonical_refuses_unc_without_connection():
    """NET-SEC-01: realpath auf einen UNC-Pfad nur mit ausdruecklicher Erlaubnis."""
    with pytest.raises(ValueError):
        system_api._canonical(WATCH_UNC)


def test_status_rejects_unknown_fields():
    with pytest.raises(ValueError):
        nfs.status.update(nfs.WATCH, password=TEST_SMB_PASSWORD)


@pytest.mark.asyncio
async def test_validation_errors_do_not_echo_password(client):
    """422-Antworten spiegeln den Body nicht zurueck (sonst stuende das Passwort darin)."""
    resp = await client.put("/api/system/settings", json={"watch_dir_net_password": TEST_SMB_PASSWORD, "watch_poll_interval": 5})
    assert resp.status_code == 422
    assert TEST_SMB_PASSWORD not in resp.text
    assert {e["loc"][-1] for e in resp.json()["detail"]} >= {"watch_dir", "watch_poll_interval"}
    too_long = await client.post("/api/system/settings/test-folder", json={
        "target": "watch", "path": WATCH_UNC, "net_user": TEST_SMB_USER, "net_password": TEST_SMB_PASSWORD * 30,
    })
    assert too_long.status_code == 200 and too_long.json()["ok"] is False
    assert TEST_SMB_PASSWORD not in too_long.text
