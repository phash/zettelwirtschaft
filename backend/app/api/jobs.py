import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.processing_job import JobStatus, ProcessingJob
from app.schemas.processing_job import JobStatusResponse, PaginatedJobsResponse
from app.services.queue_worker_service import is_queue_paused, pause_queue, resume_queue

logger = logging.getLogger("zettelwirtschaft.api.jobs")

router = APIRouter()


@router.get("/jobs", response_model=PaginatedJobsResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> PaginatedJobsResponse:
    """Liste aller Verarbeitungsjobs mit Paginierung und optionalem Status-Filter.

    Status kann ein einzelner Wert oder kommagetrennte Liste sein (z.B. 'PROCESSING,PENDING').
    """
    query = select(ProcessingJob)
    count_query = select(func.count(ProcessingJob.id))

    if status is not None:
        status_values = [s.strip() for s in status.split(",")]
        valid_statuses = [JobStatus(s) for s in status_values if s in JobStatus.__members__]
        if valid_statuses:
            query = query.where(ProcessingJob.status.in_(valid_statuses))
            count_query = count_query.where(ProcessingJob.status.in_(valid_statuses))

    # Total zaehlen
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginiert abrufen
    offset = (page - 1) * page_size
    query = query.order_by(ProcessingJob.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    jobs = result.scalars().all()

    return PaginatedJobsResponse(
        items=[JobStatusResponse.model_validate(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/jobs/queue-status")
async def get_queue_status() -> dict:
    """Gibt den aktuellen Status der Verarbeitungs-Queue zurueck."""
    return {"paused": is_queue_paused()}


@router.post("/jobs/pause")
async def pause_job_queue() -> dict:
    """Pausiert die Verarbeitungs-Queue (laufende Jobs werden abgeschlossen)."""
    pause_queue()
    return {"paused": True, "message": "Queue pausiert"}


@router.post("/jobs/resume")
async def resume_job_queue() -> dict:
    """Setzt die Verarbeitungs-Queue fort."""
    resume_queue()
    return {"paused": False, "message": "Queue fortgesetzt"}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Setzt einen fehlgeschlagenen Job auf PENDING zurueck."""
    result = await db.execute(
        select(ProcessingJob).where(ProcessingJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job nicht gefunden")
    if job.status != JobStatus.FAILED:
        raise HTTPException(400, "Nur fehlgeschlagene Jobs koennen wiederholt werden")
    job.status = JobStatus.PENDING
    job.error_message = None
    job.retry_count = 0
    await db.commit()
    logger.info("Job %s auf PENDING zurueckgesetzt", job_id)
    return {"message": "Job wird erneut verarbeitet", "job_id": job_id}
