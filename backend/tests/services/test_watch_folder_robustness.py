r"""Robustheit und FH-01-Garantien des Watch-Ordners (Review-Befunde
NET-SEC-02/04/06/07, R1, R3-R6, R9, R11) und des Exports.

Kein echtes SMB: gefaelschte WNet-API (conftest ``fake_share_api``), ein
tmp-Ordner spielt die "Freigabe"."""

import asyncio
import os
import threading
import types
from pathlib import Path

import pytest

from app.core import network_share as ns
from app.services import network_folder_service as nfs
from app.services import upload_service
from app.services import watch_folder_service as wfs
from app.services.crypto_service import encrypt_password
from tests.credentials import TEST_EMAIL_ENCRYPTION_KEY, TEST_SMB_PASSWORD, TEST_SMB_USER

BS = "\\"
WATCH_UNC = BS * 2 + "nas" + BS + "scans" + BS + "eingang"
ACCESS_ROOT = BS * 2 + "198.51.100.20" + BS + "scans"


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
    monkeypatch.setattr(wfs, "_DRAIN_TIMEOUT", 0.05)


@pytest.fixture
def imported(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_import(file_path, file_size, watch_dir, session_factory, settings, access=wfs.LOCAL):
        calls.append((file_path.name, file_size))
        return wfs.IMPORTED

    monkeypatch.setattr(wfs, "_import_file", fake_import)
    return calls


def _target(path: Path, token: str, interval: float = 0.05, network: bool = True) -> wfs.WatchTarget:
    config = nfs.FolderConfig(WATCH_UNC if network else str(path), TEST_SMB_USER if network else "", token)
    return wfs.WatchTarget(path=path, network=network, config=config, poll_interval=interval)


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


def _state():
    return (nfs.status.get(nfs.WATCH) or {}).get("state")


def _live_emitters() -> int:
    return len([t for t in threading.enumerate() if type(t).__name__ == "PollingEmitter" and t.is_alive()])


@pytest.mark.asyncio
class TestNoUncAccessWithoutConnection:
    async def test_lost_use_record_tears_down_before_reconnecting(
        self, key_settings, share_dir, token, fast, imported, fake_share_api, test_session_factory,
    ):
        """NET-SEC-02: fehlt der Verbindungseintrag, wird nicht unter laufendem
        Observer neu verbunden — erst Sitzung beenden, dann neu aufbauen."""
        at_add: list[tuple[str | None, int]] = []
        fake_share_api.add_hook = lambda remote: at_add.append((_state(), _live_emitters()))
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        try:
            await _until(lambda: _state() == "ok")
            first = len(at_add)
            fake_share_api.connected.clear()  # z.B. "net use /delete" eines anderen Prozesses
            await _until(lambda: len(at_add) > first and _state() == "ok")
        finally:
            await _cancel(task)
        state, emitters = at_add[first]
        assert state == "verbinde" and emitters == 0

    async def test_observer_snapshot_requires_own_connection(self, share_dir, token, fake_share_api, monkeypatch):
        """NET-SEC-02: der Polling-Observer liest den Ordner nur, solange der
        eigene Verbindungseintrag besteht."""
        ns.connections.acquire(nfs.WATCH, BS * 2 + "nas" + BS + "scans", ns.NetworkCredential(TEST_SMB_USER, TEST_SMB_PASSWORD))
        listdir = wfs._guarded_listdir(nfs.WATCH)
        assert listdir(str(share_dir)) == []
        scanned = []
        monkeypatch.setattr(wfs.os, "scandir", lambda p: scanned.append(p) or iter(()))
        fake_share_api.connected.clear()
        with pytest.raises(OSError):
            listdir(str(share_dir))
        assert scanned == []

    async def test_cancel_during_import_keeps_connection_until_thread_done(
        self, key_settings, share_dir, token, fast, fake_share_api, test_session_factory, sample_pdf, monkeypatch,
    ):
        """R6/NET-SEC-02: Abbruch (Einstellungen gespeichert) waehrend ein Import
        das Original wegraeumt — die Verbindung bleibt, bis der Thread fertig
        ist, und der Import wird zu Ende gefuehrt (kein Doppelimport)."""
        in_finish, go_on = threading.Event(), threading.Event()
        real_finish = wfs._finish_original
        seen = []

        def slow_finish(file_path, watch_dir, settings):
            in_finish.set()
            go_on.wait(5)
            seen.append(ns.is_connected(ACCESS_ROOT))
            return real_finish(file_path, watch_dir, settings)

        monkeypatch.setattr(wfs, "_finish_original", slow_finish)
        (share_dir / "brief.pdf").write_bytes(sample_pdf.read_bytes())
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        await asyncio.to_thread(in_finish.wait, 5)
        await _cancel(task)
        assert ns.is_connected(ACCESS_ROOT)  # Trennen aufgeschoben
        go_on.set()
        await _until(lambda: not ns.is_connected(ACCESS_ROOT))
        assert seen == [True]
        assert (share_dir / wfs.PROCESSED_DIRNAME / "brief.pdf").exists()
        assert not (share_dir / "brief.pdf").exists()

    async def test_file_arriving_during_initial_scan_is_imported(
        self, key_settings, share_dir, token, fast, imported, fake_share_api, test_session_factory, monkeypatch,
    ):
        """R4: der Observer startet VOR dem Einlesen — eine Datei, die waehrend
        des Einlesens ankommt, loest ein Ereignis aus."""
        (share_dir / "alt.pdf").write_bytes(b"%PDF-1.4 alt")
        real_list = wfs._list_candidates
        created = []

        def list_and_drop(watch_dir, settings):
            files = real_list(watch_dir, settings)
            if not created:
                (share_dir / "waehrend.pdf").write_bytes(b"%PDF-1.4 neu")
                created.append(True)
            return files

        monkeypatch.setattr(wfs, "_list_candidates", list_and_drop)
        task = asyncio.create_task(wfs._run_network_watch(test_session_factory, key_settings, _target(share_dir, token)))
        try:
            await _until(lambda: {"alt.pdf", "waehrend.pdf"} <= {n for n, _ in imported})
        finally:
            await _cancel(task)


@pytest.mark.asyncio
class TestStability:
    @pytest.mark.skipif(os.name != "nt", reason="Sperrpruefung nur unter Windows")
    async def test_open_file_is_locked(self, tmp_path):
        f = tmp_path / "offen.pdf"
        with open(f, "wb") as fh:
            fh.write(b"x")
            assert wfs._is_locked(f) is True
        assert wfs._is_locked(f) is False

    @pytest.mark.skipif(os.name != "nt", reason="Sperrpruefung nur unter Windows")
    @pytest.mark.parametrize("via_scan", [False, True])
    async def test_scanner_pausing_between_pages_is_not_imported_truncated(
        self, test_settings, test_session_factory, tmp_path, fast, imported, via_scan,
    ):
        """R5: ein Scanner, der zwischen den Seiten pausiert (Datei offen),
        wird erst nach dem Schliessen importiert — auch beim Einlesen."""
        watch = tmp_path / "scans"
        watch.mkdir()
        target = watch / "mehrseitig.pdf"
        started = threading.Event()

        def writer():
            with open(target, "wb") as fh:
                fh.write(b"%PDF-1.4 ")
                fh.flush()
                started.set()
                for _ in range(4):
                    threading.Event().wait(0.15)  # Pause > Abfrageintervall
                    fh.write(b"seite " * 100)
                    fh.flush()

        t = threading.Thread(target=writer)
        t.start()
        await asyncio.to_thread(started.wait, 5)
        if via_scan:
            await wfs._scan_existing_files(test_session_factory, test_settings, watch)
        else:
            handler = wfs._WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), watch)
            handler.on_created(types.SimpleNamespace(is_directory=False, src_path=str(target)))
            await _until(lambda: imported, timeout=10)
        t.join(5)
        assert imported == [("mehrseitig.pdf", target.stat().st_size)]

    async def test_still_growing_file_is_left_for_later(self, test_settings, test_session_factory, tmp_path, fast, imported, monkeypatch):
        monkeypatch.setattr(wfs, "_STABILITY_MAX_POLLS", 5)
        watch = tmp_path / "scans"
        watch.mkdir()
        f = watch / "waechst.pdf"
        f.write_bytes(b"%PDF")
        sizes = iter(range(1, 100))
        monkeypatch.setattr(wfs, "_file_signature", lambda p: (next(sizes), 0))
        handler = wfs._WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), watch)
        await handler._handle_new_file(f)
        assert imported == []  # nie halb importiert
        assert f.exists()


@pytest.mark.asyncio
class TestTransientReadErrors:
    async def test_transient_read_error_leaves_file_in_place(
        self, test_settings, test_session_factory, tmp_path, sample_pdf, monkeypatch,
    ):
        """R3: ein SMB-Aussetzer beim Lesen ist keine Ablehnung — die Datei
        bleibt fuer den naechsten Versuch liegen."""
        watch = tmp_path / "scans"
        watch.mkdir()
        f = watch / "rechnung.pdf"
        f.write_bytes(sample_pdf.read_bytes())

        def broken_copy(src, dst):
            raise OSError(64, "Der angegebene Netzwerkname ist nicht mehr verfügbar")

        monkeypatch.setattr(upload_service, "_copy_source", broken_copy)
        outcome = await wfs._import_file(f, f.stat().st_size, watch, test_session_factory, test_settings)
        assert outcome == wfs.SKIPPED
        assert f.exists()
        assert not (watch / wfs.REJECTED_DIRNAME).exists()
        assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []

    async def test_validation_reads_local_copy_and_no_blocking_io_on_loop(
        self, test_settings, test_session_factory, tmp_path, sample_pdf, monkeypatch,
    ):
        """R3: Validiert wird die Kopie in UPLOAD_DIR (im Thread); Dateiabfragen
        laufen nie im Event-Loop-Thread."""
        watch = tmp_path / "scans"
        watch.mkdir()
        f = watch / "rechnung.pdf"
        f.write_bytes(sample_pdf.read_bytes())
        loop_thread = threading.get_ident()
        validated, sig_threads = [], []
        real_validate = upload_service.validate_file
        real_sig = wfs._file_signature

        def spy_validate(path, *a, **k):
            validated.append((Path(path), threading.get_ident()))
            return real_validate(path, *a, **k)

        def spy_sig(p):
            sig_threads.append(threading.get_ident())
            return real_sig(p)

        monkeypatch.setattr(upload_service, "validate_file", spy_validate)
        monkeypatch.setattr(wfs, "_file_signature", spy_sig)
        monkeypatch.setattr(wfs, "_SETTLE_DELAY", 0.0)
        monkeypatch.setattr(wfs, "_STABILITY_POLL_INTERVAL", 0.01)
        handler = wfs._WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), watch)
        await handler._handle_new_file(f)
        assert validated and validated[0][0].parent == Path(test_settings.UPLOAD_DIR)
        assert validated[0][1] != loop_thread
        assert sig_threads and loop_thread not in sig_threads


@pytest.mark.asyncio
class TestLocalWatchRecovers:
    async def test_dead_polling_observer_is_restarted(
        self, test_settings, test_session_factory, tmp_path, fast, imported,
    ):
        """R9: faellt der Polling-Observer aus (Lesefehler), meldet der Status
        das und die Ueberwachung startet neu — nie still tot und "ok"."""
        watch = tmp_path / "cifs"
        watch.mkdir()
        target = _target(watch, "", interval=0.05, network=False)
        task = asyncio.create_task(wfs._run_local_watch(test_session_factory, test_settings, target, polling=True))
        try:
            await _until(lambda: _state() == "ok")
            for p in watch.iterdir():
                p.rmdir() if p.is_dir() else p.unlink()
            watch.rmdir()  # z.B. CIFS-Volume weg
            await _until(lambda: _state() == "fehler")
            assert nfs.status.get(nfs.WATCH)["next_retry_at"]
            await _until(lambda: _state() == "ok")  # neu gestartet (Ordner wieder angelegt)
            (watch / "neu.pdf").write_bytes(b"%PDF-1.4 x")
            await _until(lambda: "neu.pdf" in {n for n, _ in imported})
        finally:
            await _cancel(task)

    async def test_mkdir_failure_is_retried(self, test_settings, test_session_factory, tmp_path, fast, imported, monkeypatch):
        """R9: ist der Ordner beim Start nicht verfuegbar, wird es mit Backoff
        erneut versucht statt die Ueberwachung zu beenden."""
        watch = tmp_path / "spaeter"
        real = wfs._check_local_dir
        attempts = []

        def flaky(path, create):
            attempts.append(create)
            if len(attempts) == 1:
                raise OSError(53, "Der Netzwerkpfad wurde nicht gefunden")
            return real(path, create)

        monkeypatch.setattr(wfs, "_check_local_dir", flaky)
        task = asyncio.create_task(wfs._run_local_watch(
            test_session_factory, test_settings, _target(watch, "", network=False), polling=True,
        ))
        try:
            await _until(lambda: _state() == "fehler")
            await _until(lambda: _state() == "ok")
        finally:
            await _cancel(task)

    async def test_link_to_share_created_later_stops_local_watch(
        self, test_settings, test_session_factory, tmp_path, fast, imported, monkeypatch,
    ):
        """NET-SEC-07: eine nachtraeglich angelegte Verknuepfung auf eine
        Freigabe wird zur Laufzeit erkannt — nicht mit dem Computerkonto lesen."""
        watch = tmp_path / "lokal"
        watch.mkdir()
        task = asyncio.create_task(wfs._run_local_watch(
            test_session_factory, test_settings, _target(watch, "", network=False), polling=True,
        ))
        try:
            await _until(lambda: _state() == "ok")
            monkeypatch.setattr(
                ns, "_link_target",
                lambda p: BS * 2 + "angreifer" + BS + "x" if os.path.normcase(p) == os.path.normcase(str(watch)) else None,
            )
            await _until(lambda: _state() == "fehler")
            assert "Verknüpfung" in nfs.status.get(nfs.WATCH)["reason"]
        finally:
            await _cancel(task)


class TestHealth:
    def test_reconnect_after_failure_stays_warning(self):
        """R11: ein neuer Versuch nach einem Fehler laesst die Warnung (mit Grund) stehen."""
        nfs.status.update(nfs.WATCH, mode="netzwerk", state="verbinde", path=WATCH_UNC, user="u")
        assert nfs.health_component(nfs.WATCH)["status"] == "ok"  # erster Start
        nfs.status.update(nfs.WATCH, state="getrennt", reason="Netzwerkordner: Server nicht gefunden")
        nfs.status.update(nfs.WATCH, state="verbinde")
        comp = nfs.health_component(nfs.WATCH)
        assert comp["status"] == "warning"
        assert "Server nicht gefunden" in comp["message"]
        nfs.status.update(nfs.WATCH, state="ok")
        assert nfs.health_component(nfs.WATCH)["status"] == "ok"

    def test_export_status_reports_check_time(self):
        """R11: der Export wird nur beim Archivieren geprueft — die Anzeige
        nennt, wann zuletzt."""
        nfs.status.update(nfs.EXPORT, mode="netzwerk", state="ok", path=WATCH_UNC, user="u")
        assert nfs.health_component(nfs.EXPORT)["checked_at"]


class TestFindRemoteLink:
    def test_plain_local_path(self, tmp_path):
        assert ns.find_remote_link(str(tmp_path / "a" / "b")) is None

    def test_link_component_to_unc_is_found_without_following(self, tmp_path, monkeypatch):
        mid = tmp_path / "mid"
        mid.mkdir()
        targets = {os.path.normcase(str(mid)): "\\??\\UNC\\angreifer\\x"}
        monkeypatch.setattr(ns, "_link_target", lambda p: targets.get(os.path.normcase(p)))
        assert ns.find_remote_link(str(mid / "eingang")) == str(mid)

    def test_chain_of_local_links_to_unc_is_found(self, tmp_path, monkeypatch):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        targets = {os.path.normcase(str(a)): str(b), os.path.normcase(str(b)): BS * 2 + "srv" + BS + "share"}
        monkeypatch.setattr(ns, "_link_target", lambda p: targets.get(os.path.normcase(p)))
        assert ns.find_remote_link(str(a / "x")) == str(a)

    def test_volume_mount_point_is_local(self, tmp_path, monkeypatch):
        mp = tmp_path / "mp"
        mp.mkdir()
        targets = {os.path.normcase(str(mp)): "\\??\\Volume{12345678-1234-1234-1234-123456789abc}\\"}
        monkeypatch.setattr(ns, "_link_target", lambda p: targets.get(os.path.normcase(p)))
        assert ns.find_remote_link(str(mp / "x")) is None

    @pytest.mark.skipif(os.name != "nt", reason="Junctions nur unter Windows")
    def test_real_junction_to_local_dir_is_fine(self, tmp_path):
        import subprocess

        real = tmp_path / "echt"
        real.mkdir()
        junction = tmp_path / "j"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(real)], check=True, capture_output=True)
        assert ns.find_remote_link(str(junction / "x")) is None


class TestStartupDirs:
    def test_unc_watch_and_export_dirs_are_never_created(self, test_settings, monkeypatch, caplog):
        """NET-SEC-06: kein mkdir auf einen UNC-Pfad beim Start (Computerkonto)."""
        from app.core import data_dirs

        settings = test_settings.model_copy(update={
            "WATCH_DIR": WATCH_UNC, "EXPORT_DIR": BS * 2 + "nas" + BS + "export",
        })
        made = []
        monkeypatch.setattr(data_dirs.Path, "mkdir", lambda self, *a, **k: made.append(str(self)))
        data_dirs.ensure_startup_dirs(settings)
        assert made and not [p for p in made if p.startswith(BS * 2)]
        assert "Netzwerkpfad" in caplog.text


@pytest.mark.asyncio
class TestShutdownOrder:
    async def test_restarted_watch_task_is_stopped_before_shares_are_disconnected(self, monkeypatch):
        """NET-SEC-04/R1: auch ein ueber die Einstellungen neu gestarteter
        Watch-Task wird beendet — und erst danach (nach den Hintergrund-
        Vorgaengen) werden die Verbindungen getrennt."""
        from app import main
        from app.api import system as system_api

        order: list[str] = []

        async def restarted_watch():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                order.append("watch-stopped")
                raise

        original = asyncio.create_task(asyncio.sleep(0))
        await original  # wie der urspruengliche Task nach einem Neustart: schon fertig
        app = types.SimpleNamespace(state=types.SimpleNamespace(watch_task=asyncio.create_task(restarted_watch())))
        await asyncio.sleep(0)

        async def fake_shutdown_tasks():
            order.append("tasks")

        monkeypatch.setattr("app.services.task_service.shutdown_tasks", fake_shutdown_tasks)
        monkeypatch.setattr(ns.connections, "shutdown", lambda *a, **k: order.append("shares"))
        await main._shutdown_background(app, [original])
        assert order == ["watch-stopped", "tasks", "shares"]
        # waehrend des Beendens wird kein neuer Watch-Task gestartet
        app.state.session_factory = object()
        await system_api._restart_watch_task(app, None)
        assert app.state.watch_task.done()
