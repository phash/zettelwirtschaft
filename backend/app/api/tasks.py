"""Status-Abfrage fuer Hintergrund-Vorgaenge (siehe app.services.task_service)."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.background_task import ACTIVE_TASK_STATUSES, BackgroundTask
from app.schemas.background_task import TaskResponse
from app.services.task_service import TaskConflictError

router = APIRouter()


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    kind: str | None = None,
    key: str | None = None,
    active: bool = False,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[TaskResponse]:
    """Vorgaenge, neueste zuerst. ``active=true`` nur PENDING/RUNNING.

    Typischer Einsatz: beim Mount einer View pruefen, ob fuer ein Objekt
    (``kind`` + ``key``) noch ein Vorgang laeuft, und dessen Polling fortsetzen.
    """
    query = select(BackgroundTask)
    if kind is not None:
        query = query.where(BackgroundTask.kind == kind)
    if key is not None:
        query = query.where(BackgroundTask.key == key)
    if active:
        query = query.where(BackgroundTask.status.in_(ACTIVE_TASK_STATUSES))
    query = query.order_by(BackgroundTask.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return [TaskResponse.model_validate(t) for t in result.scalars().all()]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db, scope="function")) -> TaskResponse:
    task = await db.get(BackgroundTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Vorgang nicht gefunden")
    return TaskResponse.model_validate(task)


async def task_conflict_handler(_request: Request, exc: TaskConflictError) -> JSONResponse:
    """409 mit dem bereits laufenden Vorgang — das Frontend pollt dann diesen."""
    return JSONResponse(
        status_code=409,
        content={
            "detail": exc.message,
            "task": TaskResponse.model_validate(exc.active_task).model_dump(mode="json"),
        },
    )
