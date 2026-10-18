r"""Tests fuer app.core.network_share — UNC-Form, Fehlermeldungen, Verbinden,
Connection-Manager und Laufwerksbuchstaben. Kein echtes SMB: die
Windows-API ist per conftest (``fake_share_api``) gefaelscht."""

import pytest

from app.core import network_share as ns
from tests.credentials import TEST_SMB_PASSWORD, TEST_SMB_PASSWORD_2, TEST_SMB_USER

BS = "\\"
CRED = ns.NetworkCredential(TEST_SMB_USER, TEST_SMB_PASSWORD)


def unc(*parts: str) -> str:
    return BS * 2 + BS.join(parts)


class TestMessages:
    @pytest.mark.parametrize("code, fragment", [
        (5, "Zugriff verweigert"),
        (53, "Server nicht gefunden"),
        (67, "Die Freigabe gibt es auf dem Server nicht"),
        (64, "unterbrochen"),
        (86, "Passwort ist falsch"),
        (121, "Zeitüberschreitung"),
        (1236, "Zeitüberschreitung"),
        (1203, "nicht erreichbar"),
        (1219, "anderen Benutzer"),
        (1222, "Kein Netzwerk"),
        (1244, "Nicht angemeldet"),
        (1326, "Anmeldung fehlgeschlagen"),
        (1327, "Konto-Einschränkung"),
        (1330, "abgelaufen"),
        (1331, "deaktiviert"),
        (1909, "gesperrt"),
        (2202, "Ungültiger Benutzername"),
    ])
    def test_known_codes(self, code, fragment):
        msg = ns.message_for(code)
        assert msg.startswith("Netzwerkordner: ")
        assert fragment in msg

    def test_unknown_code(self):
        assert ns.message_for(4711) == "Netzwerkordner: Verbindung fehlgeschlagen (Windows-Fehler 4711)."

    def test_extended_error_uses_provider_text_shortened(self):
        msg = ns.message_for(1208, "Der  Server\r\nmeldet " + "x" * 500)
        assert msg.startswith("Netzwerkordner: Der Server meldet ")
        assert len(msg) <= len("Netzwerkordner: ") + 200

    def test_credential_repr_masks_password(self):
        assert TEST_SMB_PASSWORD not in repr(CRED)
        assert TEST_SMB_PASSWORD not in str(CRED)
        assert TEST_SMB_USER in repr(CRED)


class TestParseUnc:
    def test_valid_forms(self):
        p = ns.parse_unc(unc("nas", "scans", "eingang"))
        assert (p.host, p.share, p.rest) == ("nas", "scans", ("eingang",))
        assert p.root == unc("nas", "scans")
        assert ns.parse_unc("//198.51.100.20/scans/").path == unc("198.51.100.20", "scans")
        assert ns.parse_unc(unc("nas.fritz.box", "Scans Büro", "Eingang Ä")).share == "Scans Büro"

    @pytest.mark.parametrize("bad", [
        unc("nas"),                                 # ohne Freigabe
        unc("nas", "", "x"),                        # leeres Segment
        unc("nas", "scans", "..", "x"),
        unc("nas", "C$"),
        unc("nas", "ADMIN$"),
        unc("nas", "IPC$"),
        unc("nas", "print$"),
        unc("nas@SSL@443", "scans"),                # WebDAV
        unc("nas", "DavWWWRoot", "x"),
        unc("[fe80::1]", "scans"),                  # IPv6
        unc("fe80--1.ipv6-literal.net", "scans"),
        unc("nas:445", "scans"),                    # Port
        unc("na_s", "scans"),                       # ungueltiger Hostname
        unc("nas", "sc*ans"),
        unc("nas", "C$ "),                          # NET-SEC-10: Win32 kuerzt Leerzeichen/Punkte
        unc("nas", "C$."),
        unc("nas", "C$.", "Windows"),
        unc("nas", "scans", "ordner."),
        BS * 2 + "?" + BS + "UNC" + BS + "nas" + BS + "scans",
        BS * 2 + "." + BS + "C:" + BS + "x",
        r"C:\lokal",
    ])
    def test_rejected(self, bad):
        with pytest.raises(ns.UncPathError):
            ns.parse_unc(bad)

    def test_share_key_is_case_insensitive(self):
        assert ns.share_key(unc("NAS", "Scans", "a")) == ns.share_key(unc("nas", "scans", "b"))
        assert ns.share_key(r"C:\x") is None


class TestLocalHost:
    @pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.1", "127.5.5.5", "foo.localhost", "::1"])
    def test_names_rejected(self, host):
        with pytest.raises(ns.UncPathError):
            ns.check_host_not_local(host)

    def test_dns_alias_to_loopback_rejected(self, fake_share_api):
        fake_share_api.resolve_map["meinpc.fritz.box"] = ["127.0.0.1"]
        with pytest.raises(ns.UncPathError):
            ns.check_host_not_local("meinpc.fritz.box")

    def test_dns_alias_to_own_address_rejected(self, fake_share_api, monkeypatch):
        import socket

        fake_share_api.resolve_map[socket.gethostname().lower()] = ["198.51.100.33"]
        fake_share_api.resolve_map["alias.fritz.box"] = ["198.51.100.33"]
        ns._reset_caches()
        with pytest.raises(ns.UncPathError):
            ns.check_host_not_local("alias.fritz.box")

    def test_foreign_host_allowed(self, fake_share_api):
        fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
        ns.check_host_not_local("nas")  # keine Ausnahme

    def test_unresolvable_host_fails_closed(self, fake_share_api):
        """NET-SEC-08: was nicht aufloesbar ist, laesst sich nicht pruefen."""
        fake_share_api.resolve_map["geist"] = []
        with pytest.raises(ns.HostNotFound):
            ns.check_host_not_local("geist")

    def test_new_own_address_is_detected_without_restart(self, fake_share_api):
        """NET-SEC-08: keine Prozess-Caches — eine neue eigene Adresse (DHCP,
        VPN) wird sofort als lokal erkannt."""
        import socket

        own = socket.gethostname().lower()
        fake_share_api.resolve_map["alias"] = ["198.51.100.77"]
        fake_share_api.resolve_map[own] = ["198.51.100.33"]
        ns.check_host_not_local("alias")
        fake_share_api.resolve_map[own] = ["198.51.100.33", "198.51.100.77"]
        with pytest.raises(ns.UncPathError):
            ns.check_host_not_local("alias")

    def test_ipv6_only_host_is_not_pinned(self, fake_share_api):
        fake_share_api.resolve_map["nur6"] = ["2001:db8::5"]
        with pytest.raises(ns.HostNotFound):
            ns.pinned_address("nur6")


NAS_IP = "198.51.100.20"
NAS2_IP = "198.51.100.21"


@pytest.fixture
def hosts(fake_share_api):
    fake_share_api.resolve_map.update({"nas": [NAS_IP], "nas2": [NAS2_IP]})
    return fake_share_api


def ip_root(addr: str = NAS_IP, share: str = "scans") -> str:
    return unc(addr, share)


class TestConnect:
    def test_connect_resolves_once_and_uses_checked_address(self, hosts, monkeypatch):
        """NET-SEC-08: einmal aufloesen, pruefen und ueber genau diese Adresse
        verbinden — der Redirector loest den Namen nicht erneut auf."""
        lookups = []
        real = ns._resolve_addresses
        monkeypatch.setattr(ns, "_resolve_addresses", lambda h: lookups.append(h) or real(h))
        access = ns.connect(unc("nas", "scans"), CRED)
        assert access == ip_root()
        assert lookups.count("nas") == 1
        assert hosts.calls == [("cancel", ip_root(), "force"), ("add", ip_root(), TEST_SMB_USER)]
        assert ns.is_connected(ip_root())

    def test_dns_rebinding_cannot_reach_this_machine(self, hosts, monkeypatch):
        """NET-SEC-08: liefert DNS beim zweiten Mal 127.0.0.1, spielt das keine
        Rolle — die Verbindung nutzt die beim Pruefen erhaltene Adresse."""
        answers = iter([[NAS_IP], ["127.0.0.1"]])
        monkeypatch.setattr(ns, "_resolve_addresses", lambda h: next(answers) if h == "nas" else [])
        access = ns.connect(unc("nas", "scans"), CRED)
        assert access == ip_root()
        assert not any(c[0] == "add" and "127.0.0.1" in c[1] for c in hosts.calls)

    def test_reconnect_does_not_cancel_first(self, hosts):
        """NET-SEC-02: nur vor dem ersten Verbinden wird aufgeraeumt."""
        ns.connect(unc("nas", "scans"), CRED, stale_cleanup=False)
        assert [c[0] for c in hosts.calls] == ["add"]

    def test_dfs_share_is_rejected_and_disconnected(self, hosts):
        """NET-SEC-09: DFS ueber die Freigabe-Flags, ohne der Weiterleitung zu folgen."""
        hosts.dfs_flags["scans"] = ns.SHI1005_FLAGS_DFS
        with pytest.raises(ns.ShareError) as exc:
            ns.connect(unc("nas", "scans"), CRED)
        assert "DFS" in str(exc.value)
        assert hosts.connected == {}

    def test_real_api_flags(self, monkeypatch):
        """CONNECT_TEMPORARY, ohne Laufwerksbuchstaben, nur die Freigabe-Wurzel,
        nur der SMB-Provider (NET-SEC-10: kein WebDAV als Ausweichweg)."""
        captured = {}

        class FakeFn:
            def __call__(self, res_ptr, password, user, flags):
                res = res_ptr._obj
                captured.update(local=res.lpLocalName, remote=res.lpRemoteName, type=res.dwType,
                                flags=flags, user=user, has_pw=password == TEST_SMB_PASSWORD,
                                provider=res.lpProvider)
                return 0

        if not ns.IS_WINDOWS:
            pytest.skip("ctypes-WinDLL nur unter Windows")
        api = ns._WinApi()
        api._add = FakeFn()
        api._provider = "Microsoft Windows Network"
        assert api.add(unc("nas", "scans"), TEST_SMB_USER, TEST_SMB_PASSWORD) == 0
        assert captured == {
            "local": None, "remote": unc("nas", "scans"), "type": ns.RESOURCETYPE_DISK,
            "flags": ns.CONNECT_TEMPORARY, "user": TEST_SMB_USER, "has_pw": True,
            "provider": "Microsoft Windows Network",
        }

    def test_real_provider_lookup_is_lanman(self):
        if not ns.IS_WINDOWS:
            pytest.skip("nur unter Windows")
        provider = ns._WinApi()._smb_provider()  # nur Abfrage, keine Verbindung
        assert provider and "web" not in provider.lower()

    def test_error_raises_german_message_without_password(self, hosts):
        hosts.add_result = 1208
        hosts.provider_text = f"NAS sagt nein ({TEST_SMB_PASSWORD})"
        with pytest.raises(ns.ShareError) as exc:
            ns.connect(unc("nas", "scans"), CRED)
        assert exc.value.code == 1208
        assert TEST_SMB_PASSWORD not in str(exc.value)
        assert "NAS sagt nein" in str(exc.value)

    def test_connect_rechecks_dns(self, fake_share_api):
        """Reviewer-Befund 3: der Host wird bei JEDEM Verbinden neu geprueft."""
        fake_share_api.resolve_map["nas"] = ["127.0.0.1"]
        with pytest.raises(ns.ShareError):
            ns.connect(unc("nas", "scans"), CRED)
        assert not any(c[0] == "add" for c in fake_share_api.calls)

    def test_unresolvable_host_is_not_connected(self, fake_share_api):
        """NET-SEC-08: nicht aufloesbar -> fail closed (kein WNet-Aufruf)."""
        fake_share_api.resolve_map["geist"] = []
        with pytest.raises(ns.ShareError) as exc:
            ns.connect(unc("geist", "scans"), CRED)
        assert exc.value.code == ns.ERROR_BAD_NETPATH
        assert fake_share_api.calls == []

    def test_missing_password_is_rejected_without_wnet(self, fake_share_api):
        with pytest.raises(ns.ShareError):
            ns.connect(unc("nas", "scans"), ns.NetworkCredential(TEST_SMB_USER, ""))
        assert fake_share_api.calls == []

    def test_disconnect_ignores_not_connected(self, fake_share_api, caplog):
        ns.disconnect(ip_root())  # 2250 -> keine Warnung
        assert "nicht getrennt" not in caplog.text

    def test_routine_disconnect_is_not_forced(self, hosts):
        """NET-SEC-03: routinemaessiges Trennen schliesst keine offenen Dateien."""
        mgr = ns.connections
        mgr.acquire("export", unc("nas", "scans"), CRED)
        mgr.release("export")
        assert ("cancel", ip_root()) in hosts.calls
        assert hosts.calls[-1] == ("cancel", ip_root())


class TestShareConnections:
    def test_refcount_and_last_release_disconnects(self, hosts):
        mgr = ns.connections
        assert mgr.acquire("watch", unc("nas", "scans", "eingang"), CRED) == ip_root()
        mgr.acquire("export", unc("NAS", "Scans", "fertig"), CRED)
        assert [c for c in hosts.calls if c[0] == "add"] == [("add", ip_root(), TEST_SMB_USER)]
        mgr.release("watch")
        assert ns.is_connected(ip_root())
        mgr.release("export")
        assert not ns.is_connected(ip_root())

    def test_two_names_for_one_server_share_one_connection(self, hosts):
        """Zwei Namen derselben Adresse trennen sich nicht gegenseitig die Verbindung."""
        hosts.resolve_map["nas.fritz.box"] = [NAS_IP]
        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        mgr.acquire("export", unc("nas.fritz.box", "scans"), CRED)
        mgr.release("watch")
        assert ns.is_connected(ip_root())
        assert mgr.access_path("export", unc("nas.fritz.box", "scans", "x")) == ip_root() + "\\x"

    def test_same_host_other_user_conflicts_without_wnet(self, hosts):
        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        calls = len(hosts.calls)
        with pytest.raises(ns.ShareError) as exc:
            mgr.acquire("export", unc("nas", "archiv"), ns.NetworkCredential("anderer", TEST_SMB_PASSWORD))
        assert exc.value.code == 1219
        assert len(hosts.calls) == calls

    def test_same_user_other_password_conflicts(self, hosts):
        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        with pytest.raises(ns.ShareError):
            mgr.acquire("export", unc("nas", "scans"), ns.NetworkCredential(TEST_SMB_USER, TEST_SMB_PASSWORD_2))

    def test_target_switch_releases_previous(self, hosts):
        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        mgr.acquire("watch", unc("nas2", "scans"), CRED)
        assert not ns.is_connected(ip_root())
        assert mgr.holds("watch") == unc("nas2", "scans")

    def test_user_change_on_same_share_reconnects(self, hosts):
        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        mgr.acquire("watch", unc("nas", "scans"), ns.NetworkCredential("neu", TEST_SMB_PASSWORD_2))
        assert hosts.connected[ip_root().casefold()] == "neu"

    def test_check_connected_never_reconnects(self, hosts):
        """NET-SEC-02: fehlt der Verbindungseintrag, wird NICHT unter laufenden
        Zugriffen neu verbunden — Fehler, neue Zugriffe werden abgelehnt."""
        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        lease = mgr.open_lease("watch")
        hosts.connected.clear()  # z.B. "net use /delete" eines anderen Prozesses
        adds = len([c for c in hosts.calls if c[0] == "add"])
        with pytest.raises(ns.ShareError):
            mgr.check_connected("watch")
        with pytest.raises(ns.ShareError):
            mgr.open_lease("watch")
        mgr.release("watch")
        # Waehrend der Zugriff laeuft, verweigert acquire das Neuverbinden.
        with pytest.raises(ns.ShareError) as exc:
            mgr.acquire("watch", unc("nas", "scans"), CRED)
        assert "laufen noch" in str(exc.value)
        assert len([c for c in hosts.calls if c[0] == "add"]) == adds
        lease.close()
        mgr.acquire("watch", unc("nas", "scans"), CRED)  # danach neu verbinden
        assert ns.is_connected(ip_root())

    def test_check_connected_without_entry_raises(self):
        with pytest.raises(ns.ShareError) as exc:
            ns.connections.check_connected("watch")
        assert exc.value.code == ns.ERROR_NOT_CONNECTED

    def test_release_is_deferred_until_last_lease(self, hosts):
        """NET-SEC-02/03: ein laufender Zugriff behaelt die Verbindung — getrennt
        wird erst nach seinem Ende."""
        mgr = ns.connections
        mgr.acquire("export", unc("nas", "scans"), CRED)
        lease = mgr.open_lease("export")
        mgr.release("export")
        assert ns.is_connected(ip_root())
        assert lease.access_root == ip_root()
        lease.close()
        assert not ns.is_connected(ip_root())
        lease.close()  # idempotent

    def test_lease_outlives_cancelled_task(self, hosts):
        """Thread-gebundene Lease: auch nach Abbruch des wartenden Tasks bleibt
        die Verbindung, bis der Thread fertig ist (R6/NET-SEC-02)."""
        import asyncio
        import threading

        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        started, finish = threading.Event(), threading.Event()
        seen = []

        def work():
            with mgr.lease("watch"):
                started.set()
                finish.wait(5)
                seen.append(ns.is_connected(ip_root()))

        async def main():
            task = asyncio.ensure_future(asyncio.to_thread(work))
            await asyncio.to_thread(started.wait, 5)
            task.cancel()
            mgr.release("watch")  # z.B. Teardown nach Abbruch
            assert ns.is_connected(ip_root())
            finish.set()
            for _ in range(100):
                if not ns.is_connected(ip_root()):
                    break
                await asyncio.sleep(0.01)

        asyncio.run(main())
        assert seen == [True]
        assert not ns.is_connected(ip_root())

    def test_wnet_calls_do_not_block_other_shares(self, hosts):
        """R7: ein haengender Verbindungsaufbau (toter Host) haelt die Verbindung
        zu einem anderen Server nicht auf."""
        import threading
        import time

        release = threading.Event()

        def hook(remote):
            if NAS_IP in remote:
                release.wait(5)

        hosts.add_hook = hook
        mgr = ns.connections
        t = threading.Thread(target=lambda: mgr.acquire("watch", unc("nas", "scans"), CRED))
        t.start()
        time.sleep(0.05)
        started = time.monotonic()
        mgr.acquire("export", unc("nas2", "scans"), ns.NetworkCredential("x", TEST_SMB_PASSWORD_2))
        elapsed = time.monotonic() - started
        release.set()
        t.join(5)
        assert elapsed < 1.0

    def test_shutdown_waits_for_leases_then_refuses_new_connections(self, hosts):
        """NET-SEC-04: nach shutdown kein (Wieder-)Verbinden mehr."""
        import threading

        mgr = ns.connections
        mgr.acquire("watch", unc("nas", "scans"), CRED)
        mgr.acquire("export", unc("nas2", "fertig"), ns.NetworkCredential("x", TEST_SMB_PASSWORD_2))
        lease = mgr.open_lease("watch")
        threading.Timer(0.1, lease.close).start()
        mgr.shutdown(wait=5)
        assert lease._closed  # gewartet, bis der Zugriff fertig war
        assert hosts.connected == {}
        assert mgr.holds("watch") is None
        with pytest.raises(ns.ShareError):
            mgr.acquire("watch", unc("nas", "scans"), CRED)
        assert not hosts.connected


class TestClassifyDrive:
    def test_fixed_and_removable_are_local(self, fake_share_api):
        fake_share_api.drive_types.update(D=ns.DRIVE_FIXED, U=ns.DRIVE_REMOVABLE)
        assert ns.classify_drive(r"D:\Scans").kind == "local"
        assert ns.classify_drive(r"U:\Scans").kind == "local"

    def test_remote_drive_suggests_unc(self, fake_share_api):
        fake_share_api.drive_types["V"] = ns.DRIVE_REMOTE
        fake_share_api.remote_drives["V:"] = unc("nas", "scans")
        info = ns.classify_drive(r"V:\eingang")
        assert info.kind == "network_drive"
        assert info.suggestions == (unc("nas", "scans", "eingang"),)
        assert "Vorschlag: " + unc("nas", "scans", "eingang") in ns.drive_message(info)

    def test_service_view_reads_only_console_user(self, fake_share_api):
        """SEC-NF-03: nur die Netzlaufwerke des Konsolenbenutzers."""
        fake_share_api.drive_types["V"] = ns.DRIVE_NO_ROOT_DIR
        fake_share_api.user_drives[(fake_share_api.console_sid, "V")] = unc("nas", "scans")
        fake_share_api.user_drives[("S-1-5-21-9-9-9-1002", "V")] = unc("fremd", "privat")
        info = ns.classify_drive("V:\\")
        assert info.kind == "network_drive"
        assert info.suggestions == (unc("nas", "scans"),)

    def test_no_console_user_no_suggestions(self, fake_share_api):
        fake_share_api.drive_types["V"] = ns.DRIVE_NO_ROOT_DIR
        fake_share_api.console_sid = None
        fake_share_api.user_drives[("S-1-5-21-9-9-9-1002", "V")] = unc("fremd", "privat")
        assert ns.classify_drive("V:\\").kind == "missing_drive"

    def test_real_user_reader_reads_one_sid(self, monkeypatch):
        if not ns.IS_WINDOWS:
            pytest.skip("winreg nur unter Windows")
        import winreg

        values = {"S-1-5-21-1-2-3-1001\\Network\\V": unc("nas", "scans")}
        opened = []

        class Key:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def open_key(_root, path):
            opened.append(path)
            if path not in values:
                raise OSError
            return Key(path)

        monkeypatch.setattr(winreg, "OpenKey", open_key)
        monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (values[key.path], 1))
        api = object.__new__(ns._WinApi)
        assert api.read_user_remote_path("S-1-5-21-1-2-3-1001", "V") == unc("nas", "scans")
        assert api.read_user_remote_path("S-1-5-18", "V") is None          # kein Benutzerkonto
        assert api.read_user_remote_path("S-1-5-21-1\\..\\x", "V") is None  # kein Pfad-Trick
        assert opened == ["S-1-5-21-1-2-3-1001\\Network\\V"]

    def test_real_console_user_sid_is_readable(self):
        if not ns.IS_WINDOWS:
            pytest.skip("nur unter Windows")
        sid = ns._WinApi().console_user_sid()  # nur lesend
        assert sid is None or sid.startswith("S-1-5-")
    def test_subst_is_rewritten_to_real_target(self, fake_share_api):
        fake_share_api.drive_types["S"] = ns.DRIVE_FIXED
        fake_share_api.dos_devices["S:"] = r"\??\C:\Daten\Scans"
        info = ns.classify_drive(r"S:\eingang")
        assert (info.kind, info.target) == ("subst", r"C:\Daten\Scans\eingang")

    def test_subst_to_unc_is_network_drive(self, fake_share_api):
        fake_share_api.dos_devices["S:"] = r"\??\UNC\nas\scans"
        info = ns.classify_drive(r"S:\x")
        assert info.kind == "network_drive"
        assert info.suggestions == (unc("nas", "scans", "x"),)

    def test_cdrom_rejected(self, fake_share_api):
        fake_share_api.drive_types["R"] = ns.DRIVE_CDROM
        assert ns.classify_drive(r"R:\x").kind == "unsuitable"

    def test_missing_drive(self, fake_share_api):
        fake_share_api.drive_types["Q"] = ns.DRIVE_NO_ROOT_DIR
        info = ns.classify_drive(r"Q:\x")
        assert info.kind == "missing_drive"
        assert "nicht vorhanden" in ns.drive_message(info)

    def test_non_windows_reports_unsupported(self, monkeypatch):
        monkeypatch.setattr(ns, "_api", ns._UnsupportedApi())
        assert not ns.supported()
        assert ns.classify_drive(r"V:\x").kind == "none"
        with pytest.raises(ns.NetworkShareUnsupported) as exc:
            ns.connect(unc("nas", "scans"), CRED)
        assert "nur unter Windows" in str(exc.value)
