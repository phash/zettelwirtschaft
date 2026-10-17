import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import func, select
from watchdog.events import FileCreatedEvent, FileMovedEvent

from app.config import Settings
from app.models.processing_job import JobSource, ProcessingJob
from app.services import watch_folder_service as wf
from app.services.watch_folder_service import (
    PROCESSED_DIRNAME,
    REJECTED_DIRNAME,
    _is_candidate,
    _move_to_rejected,
    _scan_existing_files,
    _WatchHandler,
)


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    monkeypatch.setattr(wf, "_SETTLE_DELAY", 0)
    monkeypatch.setattr(wf, "_STABILITY_POLL_INTERVAL", 0.01)


@pytest.fixture
def custom_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Scans"
    d.mkdir()
    return d


async def _job_count(session_factory) -> int:
    async with session_factory() as session:
        return (await session.execute(select(func.count(ProcessingJob.id)))).scalar()


async def _wait_for(condition, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await condition():
        if loop.time() > deadline:
            raise AssertionError("Timeout")
        await asyncio.sleep(0.02)


class TestMoveToRejected:
    def test_moves_into_watch_dir_rejected(self, tmp_path: Path, custom_dir: Path):
        """Abgelehnte Dateien landen im konfigurierten Ordner, nicht im .env-WATCH_DIR."""
        test_file = custom_dir / "bad.pdf"
        test_file.write_bytes(b"bad content")

        _move_to_rejected(test_file, custom_dir)

        assert (custom_dir / REJECTED_DIRNAME / "bad.pdf").exists()
        assert not test_file.exists()

    def test_same_name_rejects_both_survive(self, custom_dir: Path):
        for content in (b"erste", b"zweite"):
            f = custom_dir / "scan.pdf"
            f.write_bytes(content)
            _move_to_rejected(f, custom_dir)

        contents = sorted(p.read_bytes() for p in (custom_dir / REJECTED_DIRNAME).iterdir())
        assert contents == [b"erste", b"zweite"]


class TestCandidateFilter:
    @pytest.mark.parametrize(
        "name",
        ["scan.pdf.tmp", "scan.pdf.crdownload", "scan.pdf.part", "~$brief.pdf", ".hidden.pdf",
         "desktop.ini", "Thumbs.db", "notiz.txt"],
    )
    def test_ignored(self, test_settings: Settings, name: str):
        assert _is_candidate(Path(name), test_settings) is False

    @pytest.mark.parametrize("name", ["scan.pdf", "Foto.JPG", "seite.tif", "seite.tiff"])
    def test_accepted(self, test_settings: Settings, name: str):
        assert _is_candidate(Path(name), test_settings) is True


class TestWatchHandler:
    async def test_created_in_custom_dir_moves_original_to_processed(
        self, test_settings: Settings, test_session_factory, custom_dir: Path, sample_pdf: Path,
    ):
        handler = _WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), custom_dir)
        target = custom_dir / "rechnung.pdf"
        target.write_bytes(sample_pdf.read_bytes())

        handler.on_created(FileCreatedEvent(str(target)))
        await _wait_for(lambda: _processed_exists(custom_dir, "rechnung.pdf"))

        assert not target.exists()  # Benutzerdatei nicht geloescht, sondern verschoben
        assert await _job_count(test_session_factory) == 1

    async def test_default_watch_dir_deletes_original(
        self, test_settings: Settings, test_session_factory, sample_pdf: Path,
    ):
        watch_dir = Path(test_settings.WATCH_DIR)
        handler = _WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), watch_dir)
        target = watch_dir / "rechnung.pdf"
        target.write_bytes(sample_pdf.read_bytes())

        handler.on_created(FileCreatedEvent(str(target)))

        async def gone():
            return not target.exists()
        await _wait_for(gone)
        assert await _job_count(test_session_factory) == 1
        assert not (watch_dir / PROCESSED_DIRNAME).exists()

    async def test_rename_from_temp_file_is_imported(
        self, test_settings: Settings, test_session_factory, custom_dir: Path, sample_pdf: Path,
    ):
        handler = _WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), custom_dir)
        temp = custom_dir / "scan.pdf.part"
        temp.write_bytes(sample_pdf.read_bytes())

        handler.on_created(FileCreatedEvent(str(temp)))  # Temp-Datei: ignoriert
        await asyncio.sleep(0.1)
        assert temp.exists()
        assert await _job_count(test_session_factory) == 0

        final = custom_dir / "scan.pdf"
        temp.rename(final)
        handler.on_moved(FileMovedEvent(str(temp), str(final)))
        await _wait_for(lambda: _processed_exists(custom_dir, "scan.pdf"))
        assert await _job_count(test_session_factory) == 1

    async def test_duplicate_events_import_once(
        self, test_settings: Settings, test_session_factory, custom_dir: Path, sample_pdf: Path,
    ):
        handler = _WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), custom_dir)
        target = custom_dir / "doppelt.pdf"
        target.write_bytes(sample_pdf.read_bytes())

        handler.on_created(FileCreatedEvent(str(target)))
        handler.on_moved(FileMovedEvent(str(custom_dir / "x.tmp"), str(target)))
        await _wait_for(lambda: _processed_exists(custom_dir, "doppelt.pdf"))
        await asyncio.sleep(0.1)
        assert await _job_count(test_session_factory) == 1

    async def test_invalid_content_goes_to_rejected_of_watch_dir(
        self, test_settings: Settings, test_session_factory, custom_dir: Path,
    ):
        handler = _WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), custom_dir)
        fake = custom_dir / "fake.pdf"
        fake.write_bytes(b"MZ kein pdf")

        handler.on_created(FileCreatedEvent(str(fake)))

        async def rejected():
            return (custom_dir / REJECTED_DIRNAME / "fake.pdf").exists()
        await _wait_for(rejected)
        assert not Path(test_settings.WATCH_DIR, REJECTED_DIRNAME).exists()

    async def test_files_in_subfolders_ignored(
        self, test_settings: Settings, test_session_factory, custom_dir: Path, sample_pdf: Path,
    ):
        handler = _WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), custom_dir)
        sub = custom_dir / PROCESSED_DIRNAME
        sub.mkdir()
        inner = sub / "alt.pdf"
        inner.write_bytes(sample_pdf.read_bytes())

        handler.on_moved(FileMovedEvent(str(custom_dir / "alt.pdf"), str(inner)))
        await asyncio.sleep(0.1)
        assert inner.exists()
        assert await _job_count(test_session_factory) == 0

    async def test_file_removed_before_processing(
        self, test_settings: Settings, test_session_factory, custom_dir: Path, sample_pdf: Path,
    ):
        handler = _WatchHandler(test_settings, test_session_factory, asyncio.get_running_loop(), custom_dir)
        await handler._handle_new_file(custom_dir / "weg.pdf")
        assert await _job_count(test_session_factory) == 0


class TestStartupScan:
    async def test_scan_imports_candidates_only(
        self, test_settings: Settings, test_session_factory, custom_dir: Path, sample_pdf: Path,
    ):
        (custom_dir / "a.pdf").write_bytes(sample_pdf.read_bytes())
        (custom_dir / "b.pdf.tmp").write_bytes(b"halb")
        (custom_dir / "desktop.ini").write_bytes(b"[x]")

        await _scan_existing_files(test_session_factory, test_settings, custom_dir)

        assert (custom_dir / PROCESSED_DIRNAME / "a.pdf").exists()
        assert (custom_dir / "b.pdf.tmp").exists()
        assert (custom_dir / "desktop.ini").exists()
        async with test_session_factory() as session:
            jobs = (await session.execute(select(ProcessingJob))).scalars().all()
        assert [(j.original_filename, j.source) for j in jobs] == [("a.pdf", JobSource.WATCH_FOLDER)]


async def _processed_exists(watch_dir: Path, name: str) -> bool:
    return (watch_dir / PROCESSED_DIRNAME / name).exists()


def _make_dir_link(link: Path, target: Path) -> None:
    """Junction (Windows, braucht keine Rechte) bzw. Symlink (POSIX)."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


class TestJunctionHardening:
    """FH-04: Der Dienst (LocalSystem) darf keinen Junctions/Symlinks in
    verarbeitet/ bzw. rejected/ folgen und keine verlinkten Dateien importieren."""

    @pytest.mark.parametrize("dirname", [PROCESSED_DIRNAME, REJECTED_DIRNAME])
    def test_move_does_not_follow_junction(self, tmp_path: Path, custom_dir: Path, dirname: str):
        elsewhere = tmp_path / "Systemordner"
        elsewhere.mkdir()
        _make_dir_link(custom_dir / dirname, elsewhere)
        original = custom_dir / "scan.pdf"
        original.write_bytes(b"%PDF-1.4 angreifer")

        assert wf._move_into(original, custom_dir, dirname) is None

        assert list(elsewhere.iterdir()) == []
        assert original.exists()  # bleibt liegen statt ausserhalb zu landen

    async def test_import_with_junction_keeps_original(
        self, test_settings: Settings, test_session_factory, tmp_path: Path, custom_dir: Path, sample_pdf: Path,
    ):
        elsewhere = tmp_path / "Systemordner"
        elsewhere.mkdir()
        _make_dir_link(custom_dir / PROCESSED_DIRNAME, elsewhere)
        (custom_dir / "a.pdf").write_bytes(sample_pdf.read_bytes())

        await _scan_existing_files(test_session_factory, test_settings, custom_dir)

        assert list(elsewhere.iterdir()) == []
        assert (custom_dir / "a.pdf").exists()

    def test_regular_subfolder_still_works(self, custom_dir: Path):
        original = custom_dir / "scan.pdf"
        original.write_bytes(b"x")
        dest = wf._move_into(original, custom_dir, PROCESSED_DIRNAME)
        assert dest == custom_dir / PROCESSED_DIRNAME / "scan.pdf" and dest.exists()

    async def test_linked_source_file_is_ignored(
        self, test_settings: Settings, test_session_factory, tmp_path: Path, custom_dir: Path, sample_pdf: Path,
    ):
        secret = tmp_path / "fremd" / "geheim.pdf"
        secret.parent.mkdir()
        secret.write_bytes(sample_pdf.read_bytes())
        link = custom_dir / "geheim.pdf"
        try:
            os.symlink(secret, link)
        except OSError:
            pytest.skip("Symlinks brauchen unter Windows Developer-Mode/Adminrechte")

        await _scan_existing_files(test_session_factory, test_settings, custom_dir)
        assert await _job_count(test_session_factory) == 0
        await wf._import_file(link, secret.stat().st_size, custom_dir, test_session_factory, test_settings)
        assert await _job_count(test_session_factory) == 0
        assert secret.exists()
