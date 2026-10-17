"""API-Tests fuer /api/tasks + das 202/409-Muster der Hintergrund-Vorgaenge."""

import asyncio
from datetime import datetime

import pytest
from fastapi import Depends
from httpx import AsyncClient

from app.database import get_session_factory
from app.main import app
from app.models.background_task import BackgroundTask, TaskStatus
from app.schemas.background_task import TaskResponse
from app.services.task_service import TaskContext, TaskError, shutdown_tasks, start_task, wait_for_task

_release = asyncio.Event()


@pytest.fixture(autouse=True)
async def _cancel_leftover_tasks():
    yield
    await shutdown_tasks()


@pytest.fixture
def demo_endpoint():
    """Beispiel-Endpoint nach dem Muster, das Re-Analyse/E-Mail-Abruf/Backup nutzen."""
    global _release
    _release = asyncio.Event()

    async def demo_start(item_id: str, fail: bool = False, session_factory=Depends(get_session_factory)):
        async def work(ctx: TaskContext):
            await ctx.report_progress(10, "Starte …")
            await _release.wait()
            if fail:
                raise TaskError("Absichtlich fehlgeschlagen")
            return {"item_id": item_id, "fertig": True}

        task = await start_task(
            session_factory, "demo", work, key=item_id,
            conflict_message="Für dieses Element läuft bereits ein Vorgang.",
        )
        return TaskResponse.model_validate(task)

    app.add_api_route(
        "/api/_test/demo/{item_id}", demo_start, methods=["POST"],
        status_code=202, response_model=TaskResponse,
    )
    yield
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/_test/demo/{item_id}"]


def _assert_offset(value: str | None) -> None:
    assert value is not None
    assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None, value


class TestTaskStatusEndpoint:
    async def test_unknown_task_returns_404(self, client: AsyncClient):
        resp = await client.get("/api/tasks/does-not-exist")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Vorgang nicht gefunden"

    async def test_start_poll_success(self, client: AsyncClient, demo_endpoint):
        resp = await client.post("/api/_test/demo/doc-1")
        assert resp.status_code == 202
        started = resp.json()
        assert started["status"] == "PENDING"
        assert started["done"] is False
        assert started["kind"] == "demo" and started["key"] == "doc-1"
        _assert_offset(started["created_at"])

        _release.set()
        await wait_for_task(started["id"], timeout=5)

        resp = await client.get(f"/api/tasks/{started['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SUCCEEDED"
        assert data["done"] is True
        assert data["result"] == {"item_id": "doc-1", "fertig": True}
        assert data["error"] is None
        assert data["progress_percent"] == 100
        for field in ("created_at", "started_at", "finished_at"):
            _assert_offset(data[field])

    async def test_start_poll_failure(self, client: AsyncClient, demo_endpoint):
        started = (await client.post("/api/_test/demo/doc-1?fail=true")).json()
        _release.set()
        await wait_for_task(started["id"], timeout=5)

        data = (await client.get(f"/api/tasks/{started['id']}")).json()
        assert data["status"] == "FAILED"
        assert data["done"] is True
        assert data["error"] == "Absichtlich fehlgeschlagen"
        assert data["result"] is None

    async def test_duplicate_start_returns_409_with_active_task(self, client: AsyncClient, demo_endpoint):
        first = (await client.post("/api/_test/demo/doc-1")).json()

        resp = await client.post("/api/_test/demo/doc-1")
        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"] == "Für dieses Element läuft bereits ein Vorgang."
        assert body["task"]["id"] == first["id"]
        assert body["task"]["done"] is False

        other = await client.post("/api/_test/demo/doc-2")
        assert other.status_code == 202

        _release.set()
        await wait_for_task(first["id"], timeout=5)
        await wait_for_task(other.json()["id"], timeout=5)


class TestTaskListEndpoint:
    async def test_filters(self, client: AsyncClient, db_session):
        db_session.add_all([
            BackgroundTask(id="a", kind="reanalyze", key="doc-1", status=TaskStatus.RUNNING),
            BackgroundTask(id="b", kind="reanalyze", key="doc-1", status=TaskStatus.SUCCEEDED),
            BackgroundTask(id="c", kind="reanalyze", key="doc-2", status=TaskStatus.PENDING),
            BackgroundTask(id="d", kind="backup", key="global", status=TaskStatus.FAILED, error="x"),
        ])
        await db_session.commit()

        all_ids = {t["id"] for t in (await client.get("/api/tasks")).json()}
        assert all_ids == {"a", "b", "c", "d"}

        reanalyze = {t["id"] for t in (await client.get("/api/tasks?kind=reanalyze")).json()}
        assert reanalyze == {"a", "b", "c"}

        active_doc1 = (await client.get("/api/tasks?kind=reanalyze&key=doc-1&active=true")).json()
        assert [t["id"] for t in active_doc1] == ["a"]

        active = {t["id"] for t in (await client.get("/api/tasks?active=true")).json()}
        assert active == {"a", "c"}

        assert len((await client.get("/api/tasks?limit=2")).json()) == 2
        assert (await client.get("/api/tasks?limit=0")).status_code == 422
