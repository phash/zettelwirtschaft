import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.query_params import parse_enum_list
from app.database import get_db
from app.models.processing_job import JOB_ERROR_DUPLICATE, JobStatus, ProcessingJob
from app.models.split_batch import SplitBatch, SplitBatchStatus
from app.schemas.processing_job import JobListItem, PaginatedJobsResponse, SplitProgress
from app.services import split_service
from app.services.queue_worker_service import is_queue_paused, pause_queue, resume_queue
from app.services.upload_service import remove_job_file

logger = logging.getLogger(__name__)

router = APIRouter()


async def with_split_progress(db: AsyncSession, jobs) -> list[JobListItem]:
    """JobListItem je Job; laufende Stapel-Erkennungen bekommen den Fortschritt."""
    items = [JobListItem.model_validate(job) for job in jobs]
    batch_ids = {
        job.split_batch_id for job in jobs
        if job.status == JobStatus.PROCESSING and job.split_batch_id
    }
    if batch_ids:
        rows = (await db.execute(
            select(SplitBatch.id, SplitBatch.pages_done, SplitBatch.page_count).where(
                SplitBatch.id.in_(batch_ids), SplitBatch.status == SplitBatchStatus.DETECTING,
            )
        )).all()
        progress = {row.id: SplitProgress(pages_done=row.pages_done, page_count=row.page_count) for row in rows}
        for item in items:
            if item.status == JobStatus.PROCESSING and item.split_batch_id in progress:
                item.split_progress = progress[item.split_batch_id]
    return items


async def _get_job_or_404(db: AsyncSession, job_id: str) -> ProcessingJob:
    job = await db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "Job nicht gefunden")
    return job


@router.get("/jobs", response_model=PaginatedJobsResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PaginatedJobsResponse:
    """Liste aller Verarbeitungsjobs mit Paginierung und optionalem Status-Filter.

    Status kann ein einzelner Wert oder kommagetrennte Liste sein (z.B. 'PROCESSING,PENDING').
    Die Liste enthaelt keinen OCR-Text und kein Analyse-JSON (Dashboard pollt sie);
    die Details liefert GET /api/documents/{job_id}/status.
    """
    query = select(ProcessingJob)
    count_query = select(func.count(ProcessingJob.id))

    # Kommagetrennt, case-insensitiv; unbekannte Werte -> 422 (frueher wurde der
    # Filter still ignoriert und ALLE Jobs geliefert).
    statuses = parse_enum_list(status, JobStatus, "Job-Status")
    if statuses:
        query = query.where(ProcessingJob.status.in_(statuses))
        count_query = count_query.where(ProcessingJob.status.in_(statuses))

    # Total zaehlen
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginiert abrufen (id als Tie-Breaker fuer stabile Seiten)
    offset = (page - 1) * page_size
    query = (
        query.order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    jobs = result.scalars().all()

    return PaginatedJobsResponse(
        items=await with_split_progress(db, jobs),
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
async def retry_job(job_id: str, db: AsyncSession = Depends(get_db, scope="function")) -> dict:
    """Setzt einen fehlgeschlagenen Job auf PENDING zurueck."""
    job = await _get_job_or_404(db, job_id)
    if job.status in (JobStatus.AWAITING_SPLIT, JobStatus.SPLIT):
        raise HTTPException(
            409, "Dieser Stapel wird über den Aufteilungs-Editor bearbeitet — eine Wiederholung ist nicht möglich.",
        )
    if job.status != JobStatus.FAILED:
        raise HTTPException(400, "Nur fehlgeschlagene Jobs können wiederholt werden")
    if job.error_code == JOB_ERROR_DUPLICATE:
        raise HTTPException(
            400,
            "Diese Datei ist bereits archiviert — ein erneuter Versuch ändert nichts. "
            "Bitte den Eintrag entfernen.",
        )
    if not Path(job.file_path).is_file():
        raise HTTPException(400, "Die hochgeladene Datei ist nicht mehr vorhanden. Bitte erneut hochladen.")
    job.status = JobStatus.PENDING
    job.error_message = None
    job.error_code = None
    job.retry_count = 0
    job.processing_started_at = None
    logger.info("Job %s auf PENDING zurueckgesetzt", job_id)
    return {"message": "Job wird erneut verarbeitet", "job_id": job_id}


@router.delete("/jobs/{job_id}", status_code=204)
async def dismiss_job(
    job_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Entfernt einen fehlgeschlagenen Job (z.B. Duplikat) samt der im
    Upload-Ordner verbliebenen Datei. Archivierte Dokumente bleiben unberuehrt.

    Aufteilung: ``AWAITING_SPLIT`` = Stapel verwerfen (wie /api/split/{id}/discard),
    ``SPLIT`` = nur der Datensatz (die Teile behalten ihre Dokumente)."""
    job = await _get_job_or_404(db, job_id)
    if job.status == JobStatus.AWAITING_SPLIT and job.split_batch_id:
        batch = await split_service.get_batch(db, job.split_batch_id)
        await split_service.discard(db, batch.id, version=batch.version, settings=settings)
        return Response(status_code=204)
    if job.status == JobStatus.SPLIT:
        await db.delete(job)
        await db.commit()
        return Response(status_code=204)
    if job.status != JobStatus.FAILED:
        raise HTTPException(400, "Nur fehlgeschlagene Jobs können entfernt werden")

    upload_file = job.file_path
    protected = await split_service.protected_source_paths(db)
    await db.delete(job)
    # Datei erst nach dem Commit loeschen — scheitert er, bleibt der Job samt
    # Datei bestehen (copy+delete-Disziplin wie im archive_service).
    await db.commit()

    # Nur die eigene Job-Kopie loeschen — nie eine Datei ausserhalb der
    # Job-Ablage und nie die Quelle eines aktiven Stapels.
    if not split_service.is_protected(upload_file, protected):
        remove_job_file(upload_file, settings)

    logger.info("Fehlgeschlagener Job %s entfernt", job_id)
    return Response(status_code=204)
