import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.processing_job import JobStatus, ProcessingJob
from app.schemas.processing_job import JobStatusResponse, PaginatedJobsResponse

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
