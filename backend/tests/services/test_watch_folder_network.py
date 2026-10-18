r"""Netzwerk-Watch-Ordner (Polling, Wiederverbinden, Status) und Export auf eine
Freigabe. Kein echtes SMB: gefaelschte WNet-API (conftest ``fake_share_api``),
ein tmp-Ordner spielt die "Freigabe"."""

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core import network_share as ns
from app.services import network_folder_service as nfs
from app.services import watch_folder_service as wfs
from app.services.crypto_service import encrypt_password
from app.services.settings_service import set_db_setting
from tests.credentials import TEST_EMAIL_ENCRYPTION_KEY, TEST_SMB_PASSWORD, TEST_SMB_USER

BS = "\\"
WATCH_UNC = BS * 2 + "nas" + BS + "scans" + BS + "eingang"
ROOT = BS * 2 + "nas" + BS + "scans"


@pytest.fixture
def key_settings(test_settings):
    return test_settings.model_copy(update={"EMAIL_ENCRYPTION_KEY": TEST_EMAIL_ENCRYPTION_KEY})


@pytest.fixture
def token():
    return encrypt_password(TEST_SMB_PASSWORD, TEST_EMAIL_ENCRYPTION_KEY)


@pytest.fixture
def share_dir(tmp_path, fake_share_api) -> Path:
    fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
    d = tmp_path / "freigabe"
    d.mkdir()
    return d


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(wfs, "BACKOFF_START", 0.05)
    monkeypatch.setattr(wfs, "BACKOFF_MAX", 0.2)
    monkeypatch.setattr(wfs, "_SETTLE_DELAY", 0.0)
    monkeypatch.setattr(wfs, "_STABILITY_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(wfs, "_LOCAL_CHECK_INTERVAL", 0.02)


@pytest.fixture
def imported(monkeypatch):
    calls: list[str] = []

    async def fake_import(file_path, file_size, watch_dir, session_factory, settings, access=wfs.LOCAL):
        calls.append(file_path.name)
        return wfs.IMPORTED

    monkeypatch.setattr(wfs, "_import_file", fake_import)
    return calls


def _target(share_dir: Path, token: str, interval: float = 0.05) -> wfs.WatchTarget:
    config = nfs.FolderConfig(WATCH_UNC, TEST_SMB_USER, token)
    return wfs.WatchTarget(path=share_dir, network=True, config=config, poll_interval=interval)


async def _until(cond, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Bedingung nicht erfuellt")
        await asyncio.sleep(0.02)


async def _cancel(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
class TestNetworkWatch:
    async def test_polling_detects_new_and_existing_files(
        self, key_settings, share_dir, token, fast, imported, fake_share_api, test_session_factory,
    ):
        (share_dir / "vorher.pdf").write_bytes(b"%PDF-1.4 alt")
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        try:
            await _until(lambda: "vorher.pdf" in imported)
            await _until(lambda: (nfs.status.get(nfs.WATCH) or {}).get("state") == "ok")
            assert ns.connections.holds(nfs.WATCH) == ROOT
            await asyncio.sleep(0.1)  # erster Polling-Snapshot
            (share_dir / "neu.pdf").write_bytes(b"%PDF-1.4 neu")
            await _until(lambda: "neu.pdf" in imported)
        finally:
            await _cancel(task)
        status = nfs.status.get(nfs.WATCH)
        assert status["mode"] == "netzwerk" and status["path"] == WATCH_UNC and status["user"] == TEST_SMB_USER

    async def test_unreachable_share_backs_off_and_reconnects_with_rescan(
        self, key_settings, share_dir, token, fast, imported, fake_share_api, test_session_factory, monkeypatch,
    ):
        real_probe = wfs._probe_dir
        failures = {"left": 0}

        def flaky_probe(path):
            if failures["left"] > 0:
                failures["left"] -= 1
                raise OSError(64, "Der angegebene Netzwerkname ist nicht mehr verfügbar")
            return real_probe(path)

        monkeypatch.setattr(wfs, "_probe_dir", flaky_probe)
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        try:
            await _until(lambda: (nfs.status.get(nfs.WATCH) or {}).get("state") == "ok")
            failures["left"] = 2  # NAS aus
            await _until(lambda: nfs.status.get(nfs.WATCH)["state"] == "getrennt")
            status = nfs.status.get(nfs.WATCH)
            assert status["reason"].startswith("Netzwerkordner: ")
            assert status["next_retry_at"]
            assert ns.connections.holds(nfs.WATCH) is None  # Verbindung freigegeben
            # Waehrend der Unterbrechung angekommen -> nach dem Wiederverbinden importiert
            (share_dir / "spaet.pdf").write_bytes(b"%PDF-1.4 spaet")
            await _until(lambda: "spaet.pdf" in imported)
            await _until(lambda: nfs.status.get(nfs.WATCH)["state"] == "ok")
            assert nfs.status.get(nfs.WATCH)["reason"] is None
        finally:
            await _cancel(task)
        adds = [c for c in fake_share_api.calls if c[0] == "add"]
        assert len(adds) >= 2

    async def test_no_unc_access_without_connection(
        self, key_settings, share_dir, token, fast, imported, fake_share_api, test_session_factory, monkeypatch,
    ):
        """Reviewer-Befund 2: schlaegt das Verbinden fehl, wird der Pfad nie
        angefasst (Windows meldete sonst das Computerkonto an)."""
        touched = []
        monkeypatch.setattr(wfs, "_probe_dir", lambda p: touched.append(p))

        async def no_scan(*a, **k):
            touched.append("scan")

        monkeypatch.setattr(wfs, "_scan_existing_files", no_scan)
        fake_share_api.add_result = ns.ERROR_LOGON_FAILURE
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        try:
            await _until(lambda: (nfs.status.get(nfs.WATCH) or {}).get("state") == "getrennt")
            await asyncio.sleep(0.15)  # einige Backoff-Runden
        finally:
            await _cancel(task)
        assert touched == []
        assert "Anmeldung fehlgeschlagen" in nfs.status.get(nfs.WATCH)["reason"]

    async def test_lost_use_record_is_reconnected_before_probe(
        self, key_settings, share_dir, token, fast, imported, fake_share_api, test_session_factory,
    ):
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        try:
            await _until(lambda: (nfs.status.get(nfs.WATCH) or {}).get("state") == "ok")
            adds = len([c for c in fake_share_api.calls if c[0] == "add"])
            fake_share_api.connected.clear()
            await _until(lambda: len([c for c in fake_share_api.calls if c[0] == "add"]) > adds)
        finally:
            await _cancel(task)

    async def test_cancel_releases_connection(
        self, key_settings, share_dir, token, fast, imported, fake_share_api, test_session_factory,
    ):
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        await _until(lambda: (nfs.status.get(nfs.WATCH) or {}).get("state") == "ok")
        await _cancel(task)
        assert ns.connections.holds(nfs.WATCH) is None
        assert fake_share_api.connected == {}

    async def test_missing_credentials_reported(
        self, key_settings, share_dir, fast, imported, fake_share_api, test_session_factory,
    ):
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, "")))
        try:
            await _until(lambda: (nfs.status.get(nfs.WATCH) or {}).get("state") == "getrennt")
        finally:
            await _cancel(task)
        assert "Benutzer und Passwort" in nfs.status.get(nfs.WATCH)["reason"]
        assert fake_share_api.calls == []

    async def test_drain_waits_for_running_imports(self, test_settings, test_session_factory, share_dir):
        handler = wfs._WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), share_dir)
        done = []

        async def slow():
            await asyncio.sleep(0.05)
            done.append(True)

        task = asyncio.get_running_loop().create_task(slow())
        handler._tasks.add(task)
        await handler.drain(1.0)
        assert done == [True]


def test_backoff_sequence():
    seq, cur = [], wfs.BACKOFF_START
    for _ in range(7):
        seq.append(cur)
        cur = wfs.next_backoff(cur)
    assert seq == [30, 60, 120, 240, 480, 900, 900]


def test_clamp_poll_interval():
    assert wfs.clamp_poll_interval("5") == 10
    assert wfs.clamp_poll_interval(9999) == 600
    assert wfs.clamp_poll_interval("x") == 30
    assert wfs.clamp_poll_interval(45) == 45


@pytest.mark.asyncio
async def test_resolve_watch_target(key_settings, test_session_factory, token):
    async with test_session_factory() as s:
        await set_db_setting(s, "watch_dir", WATCH_UNC)
        await set_db_setting(s, "watch_dir_net_user", TEST_SMB_USER)
        await set_db_setting(s, "watch_dir_net_password", token)
        await set_db_setting(s, "watch_poll_interval", "45")
        await s.commit()
    target = await wfs.resolve_watch_target(test_session_factory, key_settings)
    assert target.network is True
    assert target.poll_interval == 45
    assert target.config.user == TEST_SMB_USER
    assert nfs.credential_for(target.config, key_settings).password == TEST_SMB_PASSWORD


@pytest.mark.asyncio
async def test_local_watch_reports_status(test_settings, test_session_factory):
    target = await wfs.resolve_watch_target(test_session_factory, test_settings)
    assert target.network is False
    task = asyncio.create_task(wfs._run_local_watch(test_session_factory, test_settings, target))
    try:
        await _until(lambda: (nfs.status.get(nfs.WATCH) or {}).get("state") == "ok")
    finally:
        await _cancel(task)
    assert nfs.status.get(nfs.WATCH)["mode"] == "lokal"


class TestExport:
    def test_local_export_needs_nothing(self, key_settings, fake_share_api, tmp_path):
        assert nfs.prepare_export(nfs.FolderConfig(str(tmp_path), "", ""), key_settings) is True
        assert fake_share_api.calls == []

    def test_unc_without_credentials_is_skipped(self, key_settings, fake_share_api):
        assert nfs.prepare_export(nfs.FolderConfig(ROOT, "", ""), key_settings) is False
        assert fake_share_api.calls == []
        assert "Benutzer und Passwort" in nfs.status.get(nfs.EXPORT)["reason"]

    def test_expert_override_keeps_legacy_behaviour(self, key_settings, fake_share_api):
        settings = key_settings.model_copy(update={"ALLOW_NETWORK_FOLDERS": True})
        assert nfs.prepare_export(nfs.FolderConfig(ROOT, "", ""), settings) is True
        assert fake_share_api.calls == []

    def test_connects_once_then_checks(self, key_settings, fake_share_api, token):
        fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
        cfg = nfs.FolderConfig(ROOT + BS + "fertig", TEST_SMB_USER, token)
        assert nfs.prepare_export(cfg, key_settings) is True
        assert nfs.prepare_export(cfg, key_settings) is True
        assert len([c for c in fake_share_api.calls if c[0] == "add"]) == 1
        assert nfs.status.get(nfs.EXPORT)["state"] == "ok"
        nfs.release_export()
        assert fake_share_api.connected == {}

    def test_connect_failure_is_graceful(self, key_settings, fake_share_api, token):
        fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
        fake_share_api.add_result = ns.ERROR_BAD_NETPATH
        assert nfs.prepare_export(nfs.FolderConfig(ROOT, TEST_SMB_USER, token), key_settings) is False
        assert "Server nicht gefunden" in nfs.status.get(nfs.EXPORT)["reason"]

    def test_connect_failure_backs_off(self, key_settings, fake_share_api, token):
        """R2: nach einem Fehlschlag zahlt nicht jedes Dokument erneut den
        vollen Verbindungs-Timeout."""
        fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
        fake_share_api.add_result = ns.ERROR_SEM_TIMEOUT
        cfg = nfs.FolderConfig(ROOT, TEST_SMB_USER, token)
        assert nfs.prepare_export(cfg, key_settings) is False
        assert nfs.prepare_export(cfg, key_settings) is False
        assert len([c for c in fake_share_api.calls if c[0] == "add"]) == 1
        assert nfs.status.get(nfs.EXPORT)["next_retry_at"]
        nfs.release_export()  # Einstellungen geaendert -> sofort neu versuchen
        fake_share_api.add_result = None
        assert nfs.prepare_export(cfg, key_settings) is True

    def test_new_credentials_replace_held_connection(self, key_settings, fake_share_api):
        """R10: eine noch gehaltene Verbindung mit alten Zugangsdaten wird nicht
        weiterverwendet, sobald neue gespeichert sind."""
        from tests.credentials import TEST_SMB_PASSWORD_2

        fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
        old = nfs.FolderConfig(ROOT, TEST_SMB_USER, encrypt_password(TEST_SMB_PASSWORD, TEST_EMAIL_ENCRYPTION_KEY))
        new = nfs.FolderConfig(ROOT, TEST_SMB_USER, encrypt_password(TEST_SMB_PASSWORD_2, TEST_EMAIL_ENCRYPTION_KEY))
        passwords = []
        real_add = fake_share_api.add

        def add(remote, user, password):
            passwords.append(password)
            return real_add(remote, user, password)

        fake_share_api.add = add
        assert nfs.prepare_export(old, key_settings) is True
        fake_share_api.connected.clear()  # Verbindung weg (z.B. NAS-Neustart)
        assert nfs.prepare_export(new, key_settings) is True
        assert passwords == [TEST_SMB_PASSWORD, TEST_SMB_PASSWORD_2]

    def test_wrong_key_reports_decrypt_problem(self, test_settings, token):
        other_key = Fernet.generate_key().decode()  # Schluessel gewechselt
        settings = test_settings.model_copy(update={"EMAIL_ENCRYPTION_KEY": other_key})
        assert nfs.prepare_export(nfs.FolderConfig(ROOT, TEST_SMB_USER, token), settings) is False
        assert "entschlüsselt" in nfs.status.get(nfs.EXPORT)["reason"]


async def _archive_with_export(db_session, sample_pdf, settings, export_requests):
    from app.services import archive_service
    from tests.services.test_archive_service import _make_analysis_result, _make_ocr_result

    return await archive_service.archive_document(
        file_path=sample_pdf, original_filename="rechnung.pdf", stored_filename="abc_rechnung.pdf",
        file_type="pdf", file_size_bytes=sample_pdf.stat().st_size,
        ocr_result=_make_ocr_result(), analysis_result=_make_analysis_result(),
        settings=settings, session=db_session, export_requests=export_requests,
    )


@pytest.mark.asyncio
async def test_archive_skips_unc_export_without_connection(
    key_settings, db_session, sample_pdf, fake_share_api, monkeypatch, token,
):
    """Archivierung laeuft weiter, die Export-Kopie wird ohne Verbindung NICHT versucht."""
    from app.services import archive_service

    fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
    fake_share_api.add_result = ns.ERROR_LOGON_FAILURE
    await set_db_setting(db_session, "export_dir", ROOT + BS + "fertig")
    await set_db_setting(db_session, "export_dir_net_user", TEST_SMB_USER)
    await set_db_setting(db_session, "export_dir_net_password", token)
    copies = []
    real_copy = archive_service._copy_file
    monkeypatch.setattr(archive_service, "_copy_file", lambda s, d: copies.append(str(d)) or real_copy(s, d))
    requests: list = []
    doc = await _archive_with_export(db_session, sample_pdf, key_settings, requests)
    assert doc.id
    assert len(requests) == 1
    assert await archive_service.export_copy(requests[0], key_settings) is None
    assert not any(c.startswith(BS * 2) for c in copies)
    assert nfs.status.get(nfs.EXPORT)["state"] == "getrennt"


@pytest.mark.asyncio
async def test_network_export_runs_outside_the_archive_transaction(
    key_settings, db_session, sample_pdf, fake_share_api, token,
):
    """R2: archive_document baut keine Netzwerkverbindung auf (die Sitzung
    haelt waehrenddessen die SQLite-Schreibsperre) — verbunden wird erst bei
    export_copy, nach dem Commit des Aufrufers."""
    fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
    await set_db_setting(db_session, "export_dir", ROOT + BS + "fertig")
    await set_db_setting(db_session, "export_dir_net_user", TEST_SMB_USER)
    await set_db_setting(db_session, "export_dir_net_password", token)
    requests: list = []
    await _archive_with_export(db_session, sample_pdf, key_settings, requests)
    assert fake_share_api.calls == []
    assert requests and requests[0].config.path == ROOT + BS + "fertig"


@pytest.mark.asyncio
async def test_export_copy_holds_connection_until_done(key_settings, fake_share_api, token, tmp_path, monkeypatch):
    """NET-SEC-03: Ein Einstellungswechsel waehrend der Kopie trennt erst nach
    deren Ende — Kopie und Aufraeumen laufen nie ohne eigene Verbindung."""
    import threading

    from app.services import archive_service

    fake_share_api.resolve_map["nas"] = ["198.51.100.20"]
    share = tmp_path / "freigabe"
    share.mkdir()
    access_root = BS * 2 + "198.51.100.20" + BS + "scans"
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.4 x")
    in_copy, go_on = threading.Event(), threading.Event()
    seen = []

    def fake_copy(src, dest):
        assert str(dest).startswith(access_root)  # ueber die gepruefte Adresse
        in_copy.set()
        go_on.wait(5)
        seen.append(ns.is_connected(access_root))

    monkeypatch.setattr(archive_service, "_copy_file", fake_copy)
    request = archive_service.ExportRequest(
        archive_path=source, document_type="RECHNUNG", document_date=None, stored_filename="doc.pdf",
        scope_slug=None, config=nfs.FolderConfig(ROOT + BS + "fertig", TEST_SMB_USER, token),
    )
    task = asyncio.create_task(archive_service.export_copy(request, key_settings))
    await asyncio.to_thread(in_copy.wait, 5)
    nfs.release_export()  # PUT /system/settings mitten in der Kopie
    assert ns.is_connected(access_root)
    go_on.set()
    assert await task is not None
    assert seen == [True]
    assert not ns.is_connected(access_root)
    assert ("cancel", access_root) in fake_share_api.calls  # routinemaessig ohne force


@pytest.mark.asyncio
@pytest.mark.parametrize("allow, creds, expected", [
    (True, False, "legacy"),    # Experten-Override: bisheriges Verhalten (Polling)
    (False, False, "network"),  # ohne Override: eigener Zustandsautomat (meldet fehlende Zugangsdaten)
    (True, True, "network"),    # mit Zugangsdaten immer eigene Verbindung
])
async def test_run_watch_folder_dispatch(key_settings, test_session_factory, token, monkeypatch, allow, creds, expected):
    settings = key_settings.model_copy(update={"ALLOW_NETWORK_FOLDERS": allow})
    async with test_session_factory() as s:
        await set_db_setting(s, "watch_dir", WATCH_UNC)
        if creds:
            await set_db_setting(s, "watch_dir_net_user", TEST_SMB_USER)
            await set_db_setting(s, "watch_dir_net_password", token)
        await s.commit()
    calls = []

    async def fake_local(sf, st, target, polling=None, mode="lokal"):
        calls.append(("legacy", polling, mode))

    async def fake_network(sf, st, target):
        calls.append(("network",))

    monkeypatch.setattr(wfs, "_run_local_watch", fake_local)
    monkeypatch.setattr(wfs, "_run_network_watch", fake_network)
    await wfs.run_watch_folder(test_session_factory, settings)
    assert calls[0][0] == expected
    if expected == "legacy":
        assert calls[0][1:] == (True, "netzwerk")
