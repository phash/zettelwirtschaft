"""AVAIL-05 / AVAIL-06: Ressourcen-Guards fuer Backups, Analysen und DB-Wartung."""

import asyncio
from collections import namedtuple
from pathlib import Path

import pytest

from app.services import backup_service
from app.services.task_service import shutdown_tasks, wait_for_task
from tests.factories import make_document


@pytest.fixture(autouse=True)
async def _stop_tasks():
    yield
    await shutdown_tasks()


_Usage = namedtuple("_Usage", "total used free")


async def _run_backup(client, full: bool) -> dict:
    task = (await client.post("/api/system/backup", params={"full": full})).json()
    await wait_for_task(task["id"], timeout=10)
    return (await client.get(f"/api/tasks/{task['id']}")).json()


# --- AVAIL-05: Backups -------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_full_backups_are_rotated(client, test_settings):
    doc = Path(test_settings.ARCHIVE_DIR) / "privat" / "a.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"%PDF-1.4")
    for _ in range(5):
        final = await _run_backup(client, full=True)
        assert final["status"] == "SUCCEEDED", final
    fulls = list((test_settings.data_dir / "backups").glob("backup_full_*.zip"))
    assert len(fulls) == 3, [f.name for f in fulls]


@pytest.mark.asyncio
async def test_backup_refused_when_disk_nearly_full(client, test_settings, monkeypatch):
    monkeypatch.setattr(
        backup_service.shutil, "disk_usage", lambda _p: _Usage(10**9, 10**9 - 1024, 1024)
    )
    final = await _run_backup(client, full=True)
    assert final["status"] == "FAILED", final
    assert "Nicht genug freier Speicher" in final["error"]
    backups = test_settings.data_dir / "backups"
    assert not list(backups.glob("*.zip")) and not list(backups.glob(".partial_*"))


def test_create_backup_checks_space_for_cli_path(test_settings, monkeypatch, tmp_path):
    """Auch der Offline-Pfad (--backup, Uninstaller/Update-Wizard) prueft den Platz."""
    test_settings.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    test_settings.sqlite_db_path.write_bytes(b"x" * 1024)
    monkeypatch.setattr(
        backup_service.shutil, "disk_usage", lambda _p: _Usage(10**9, 10**9, 0)
    )
    with pytest.raises(backup_service.BackupSpaceError):
        backup_service.create_backup(test_settings, include_documents=False, out_dir=tmp_path / "out")


# --- AVAIL-06: Analyse-Slot + Re-Analyse-Deckel + DB-Wartung ------------------


@pytest.mark.asyncio
async def test_analyses_are_serialized_process_wide(monkeypatch, test_settings):
    from app.services import analysis_service

    running = 0
    peak = 0

    async def fake_unlimited(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return None, None

    monkeypatch.setattr(analysis_service, "_analyze_document_unlimited", fake_unlimited)
    await asyncio.gather(
        *(analysis_service.analyze_document(Path("x.pdf"), "pdf", test_settings) for _ in range(10))
    )
    assert peak == analysis_service.ANALYSIS_CONCURRENCY == 1


@pytest.mark.asyncio
async def test_active_reanalyses_are_capped(client, db_session, monkeypatch):
    from app.api import review

    release = asyncio.Event()

    async def blocked(ctx, document_id, settings, with_ocr=False):
        await release.wait()
        return {}

    monkeypatch.setattr(review, "run_reanalysis", blocked)
    docs = [await make_document(db_session, commit=True, ocr_text="Rechnung") for _ in range(5)]

    statuses = []
    for doc in docs:
        resp = await client.post(f"/api/review/documents/{doc.id}/reanalyze")
        statuses.append(resp.status_code)
    cap = review.MAX_ACTIVE_REANALYSES
    assert statuses[:cap] == [202] * cap
    assert set(statuses[cap:]) == {429}, statuses

    release.set()
    await shutdown_tasks()


@pytest.mark.asyncio
async def test_parallel_db_maintenance_is_rejected(client, monkeypatch):
    from app.services import search_service

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_rebuild(session):
        started.set()
        await release.wait()
        return 0

    monkeypatch.setattr(search_service, "rebuild_fts_index", slow_rebuild)
    first = asyncio.create_task(client.post("/api/system/maintenance/rebuild-index"))
    await asyncio.wait_for(started.wait(), 5)

    second = await client.post("/api/system/maintenance/rebuild-index")
    assert second.status_code == 409, second.text
    vacuum = await client.post("/api/system/maintenance/optimize-db")
    assert vacuum.status_code == 409, vacuum.text

    release.set()
    assert (await first).status_code == 200
    # Guard wieder frei
    again = await client.post("/api/system/maintenance/rebuild-index")
    assert again.status_code == 200, again.text
