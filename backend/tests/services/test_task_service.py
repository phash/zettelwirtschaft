"""Tests fuer die gemeinsame Hintergrund-Vorgangs-Infrastruktur (task_service)."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.background_task import BackgroundTask, TaskStatus
from app.models.system_setting import SystemSetting
from app.services import task_service
from app.services.task_service import (
    INTERRUPTED_MESSAGE,
    SHUTDOWN_MESSAGE,
    TaskConflictError,
    TaskContext,
    TaskError,
    find_active_task,
    get_task,
    prune_finished_tasks,
    recover_interrupted_tasks,
    shutdown_tasks,
    start_task,
    wait_for_task,
)


@pytest.fixture(autouse=True)
async def _cancel_leftover_tasks():
    yield
    # Kein Vorgang darf die Test-DB ueberleben (drop_all im Engine-Fixture).
    await shutdown_tasks()


def _blocking_work(event: asyncio.Event):
    async def work(ctx: TaskContext):
        await event.wait()
        return {"ok": True}
    return work


class TestStartTask:
    async def test_success_stores_result_and_timestamps(self, test_session_factory):
        async def work(ctx: TaskContext):
            return {"count": 3, "amount": Decimal("12.50"), "when": datetime(2026, 1, 2, tzinfo=timezone.utc)}

        task = await start_task(test_session_factory, "demo", work, key="k1")
        assert task.status == TaskStatus.PENDING
        assert task.created_at.tzinfo is not None

        await wait_for_task(task.id, timeout=5)
        done = await get_task(test_session_factory, task.id)
        assert done.status == TaskStatus.SUCCEEDED
        assert done.result == {"count": 3, "amount": 12.5, "when": "2026-01-02T00:00:00+00:00"}
        assert done.error is None
        assert done.progress_percent == 100
        assert done.started_at is not None and done.started_at.tzinfo is not None
        assert done.finished_at is not None and done.finished_at >= done.started_at

    async def test_work_runs_with_own_session(self, test_session_factory):
        async def work(ctx: TaskContext):
            async with ctx.session_factory() as session:
                session.add(SystemSetting(key="task_test", value="geschrieben"))
                await session.commit()
            return None

        task = await start_task(test_session_factory, "demo", work)
        await wait_for_task(task.id, timeout=5)

        async with test_session_factory() as session:
            row = await session.get(SystemSetting, "task_test")
        assert row is not None and row.value == "geschrieben"
        done = await get_task(test_session_factory, task.id)
        assert done.status == TaskStatus.SUCCEEDED
        assert done.result is None

    async def test_progress_is_visible_while_running(self, test_session_factory):
        release = asyncio.Event()
        reported = asyncio.Event()

        async def work(ctx: TaskContext):
            await ctx.report_progress(40, "Analysiere Dokument …")
            reported.set()
            await release.wait()
            return {"ok": True}

        task = await start_task(test_session_factory, "demo", work)
        await asyncio.wait_for(reported.wait(), timeout=5)

        running = await get_task(test_session_factory, task.id)
        assert running.status == TaskStatus.RUNNING
        assert running.progress_percent == 40
        assert running.progress_text == "Analysiere Dokument …"

        release.set()
        await wait_for_task(task.id, timeout=5)
        assert (await get_task(test_session_factory, task.id)).status == TaskStatus.SUCCEEDED

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (TaskError("Ollama nicht erreichbar"), "Ollama nicht erreichbar"),
            (HTTPException(status_code=404, detail="Dokument nicht gefunden"), "Dokument nicht gefunden"),
            (RuntimeError("boom"), "Interner Fehler: RuntimeError: boom"),
        ],
    )
    async def test_failure_is_recorded(self, test_session_factory, exc, expected):
        async def work(ctx: TaskContext):
            raise exc

        task = await start_task(test_session_factory, "demo", work)
        await wait_for_task(task.id, timeout=5)

        failed = await get_task(test_session_factory, task.id)
        assert failed.status == TaskStatus.FAILED
        assert failed.error == expected
        assert failed.result is None
        assert failed.finished_at is not None

    async def test_unserializable_result_fails_cleanly(self, test_session_factory):
        async def work(ctx: TaskContext):
            return object()

        task = await start_task(test_session_factory, "demo", work)
        await wait_for_task(task.id, timeout=5)
        failed = await get_task(test_session_factory, task.id)
        assert failed.status == TaskStatus.FAILED
        assert failed.error.startswith("Interner Fehler")


class TestDuplicateGuard:
    async def test_second_start_with_same_key_conflicts(self, test_session_factory):
        release = asyncio.Event()
        first = await start_task(test_session_factory, "reanalyze", _blocking_work(release), key="doc-1")

        with pytest.raises(TaskConflictError) as exc_info:
            await start_task(
                test_session_factory, "reanalyze", _blocking_work(release),
                key="doc-1", conflict_message="Re-Analyse läuft bereits.",
            )
        assert exc_info.value.active_task.id == first.id
        assert exc_info.value.message == "Re-Analyse läuft bereits."

        # Anderer Key / andere Art / ohne Key: kein Konflikt.
        other_key = await start_task(test_session_factory, "reanalyze", _blocking_work(release), key="doc-2")
        other_kind = await start_task(test_session_factory, "email_fetch", _blocking_work(release), key="doc-1")
        no_key_1 = await start_task(test_session_factory, "reanalyze", _blocking_work(release))
        no_key_2 = await start_task(test_session_factory, "reanalyze", _blocking_work(release))

        active = await find_active_task(test_session_factory, "reanalyze", "doc-1")
        assert active is not None and active.id == first.id

        release.set()
        for t in (first, other_key, other_kind, no_key_1, no_key_2):
            await wait_for_task(t.id, timeout=5)

        # Nach Abschluss darf derselbe (kind, key) wieder gestartet werden.
        assert await find_active_task(test_session_factory, "reanalyze", "doc-1") is None
        again = await start_task(test_session_factory, "reanalyze", _blocking_work(release), key="doc-1")
        await wait_for_task(again.id, timeout=5)
        assert (await get_task(test_session_factory, again.id)).status == TaskStatus.SUCCEEDED

    async def test_concurrent_starts_only_one_wins(self, test_session_factory):
        release = asyncio.Event()
        results = await asyncio.gather(
            *[
                start_task(test_session_factory, "backup", _blocking_work(release), key="global")
                for _ in range(3)
            ],
            return_exceptions=True,
        )
        started = [r for r in results if isinstance(r, BackgroundTask)]
        conflicts = [r for r in results if isinstance(r, TaskConflictError)]
        assert len(started) == 1
        assert len(conflicts) == 2
        assert all(c.active_task.id == started[0].id for c in conflicts)
        release.set()
        await wait_for_task(started[0].id, timeout=5)

    async def test_partial_unique_index_blocks_second_active_row(self, db_session):
        db_session.add(BackgroundTask(kind="backup", key="global", status=TaskStatus.RUNNING))
        await db_session.commit()

        # Abgeschlossene Eintraege mit gleichem Key sind erlaubt ...
        db_session.add(BackgroundTask(kind="backup", key="global", status=TaskStatus.SUCCEEDED))
        await db_session.commit()

        # ... ein zweiter aktiver nicht.
        db_session.add(BackgroundTask(kind="backup", key="global", status=TaskStatus.PENDING))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestRecovery:
    async def test_recover_marks_active_tasks_failed(self, test_session_factory):
        async with test_session_factory() as session:
            session.add_all([
                BackgroundTask(id="running", kind="reanalyze", key="d1", status=TaskStatus.RUNNING,
                               started_at=datetime.now(timezone.utc) - timedelta(seconds=30)),
                BackgroundTask(id="pending", kind="email_fetch", key="1", status=TaskStatus.PENDING),
                BackgroundTask(id="done", kind="backup", status=TaskStatus.SUCCEEDED,
                               result={"filename": "x.zip"}, finished_at=datetime.now(timezone.utc)),
            ])
            await session.commit()

        count = await recover_interrupted_tasks(test_session_factory)
        assert count == 2

        for task_id in ("running", "pending"):
            t = await get_task(test_session_factory, task_id)
            assert t.status == TaskStatus.FAILED
            assert t.error == INTERRUPTED_MESSAGE
            assert t.finished_at is not None
        done = await get_task(test_session_factory, "done")
        assert done.status == TaskStatus.SUCCEEDED
        assert done.error is None

        # Nach der Recovery ist der Key wieder frei.
        assert await find_active_task(test_session_factory, "reanalyze", "d1") is None

    async def test_prune_removes_only_old_finished_tasks(self, test_session_factory):
        now = datetime.now(timezone.utc)
        async with test_session_factory() as session:
            session.add_all([
                BackgroundTask(id="old", kind="backup", status=TaskStatus.SUCCEEDED, finished_at=now - timedelta(days=40)),
                BackgroundTask(id="old-failed", kind="backup", status=TaskStatus.FAILED, finished_at=now - timedelta(days=31)),
                BackgroundTask(id="recent", kind="backup", status=TaskStatus.SUCCEEDED, finished_at=now - timedelta(days=1)),
                BackgroundTask(id="active", kind="backup", status=TaskStatus.RUNNING,
                               created_at=now - timedelta(days=40)),
            ])
            await session.commit()

        assert await prune_finished_tasks(test_session_factory, older_than_days=30) == 2
        async with test_session_factory() as session:
            ids = set((await session.execute(select(BackgroundTask.id))).scalars())
        assert ids == {"recent", "active"}

    async def test_shutdown_marks_running_task_failed(self, test_session_factory):
        started = asyncio.Event()

        async def work(ctx: TaskContext):
            started.set()
            await asyncio.Event().wait()  # laeuft "ewig"

        task = await start_task(test_session_factory, "reanalyze", work, key="d1")
        await asyncio.wait_for(started.wait(), timeout=5)

        await shutdown_tasks()

        t = await get_task(test_session_factory, task.id)
        assert t.status == TaskStatus.FAILED
        assert t.error == SHUTDOWN_MESSAGE
        assert task.id not in task_service._running
