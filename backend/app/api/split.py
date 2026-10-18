"""Aufteilen von Stapel-Scans (Spezifikation 12) — ``/api/split``.

Die API rendert nichts: Seiten-Vorschaubilder erzeugt der Queue-Worker bei der
Erkennung, hier werden sie nur ausgeliefert. Statuswechsel laufen ueber
``split_service`` (optimistisches Locking, 409 bei Konflikt).
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.query_params import parse_enum_list
from app.database import get_db, get_session_factory
from app.models.document import Document
from app.models.processing_job import ProcessingJob
from app.models.split_batch import SplitBatch, SplitBatchStatus
from app.schemas.split import (
    PaginatedSplitBatches,
    SplitBatchListItem,
    SplitBatchResponse,
    SplitBoundary,
    SplitConfirmRequest,
    SplitConfirmResponse,
    SplitPage,
    SplitPartInfo,
    SplitRequestResponse,
    SplitRetryResponse,
    SplitUndoResponse,
    SplitVersionRequest,
)
from app.services import split_service
from app.services.split_service import SplitError

logger = logging.getLogger(__name__)

router = APIRouter()


async def split_error_handler(_request: Request, exc: SplitError) -> JSONResponse:
    """SplitError -> HTTP-Status der Klasse, deutscher Text in ``detail``,
    maschinenlesbare Ursache (z.B. SPLIT_SOURCE_MISSING) in ``code``."""
    body = {"detail": exc.message}
    code = getattr(exc, "code", None)
    if code:
        body["code"] = code
    return JSONResponse(status_code=exc.status_code, content=body)


def _proposal_counts(batch: SplitBatch) -> tuple[int, int]:
    """(vorgeschlagene Teile, unsichere Uebergaenge)."""
    boundaries = split_service.load_proposal(batch).get("boundaries", [])
    cuts = sum(1 for b in boundaries if b.get("decision") == "cut")
    return 1 + cuts, sum(1 for b in boundaries if b.get("decision") == "uncertain")


async def build_batch_response(db: AsyncSession, batch: SplitBatch) -> SplitBatchResponse:
    proposal = split_service.load_proposal(batch)
    final = split_service.load_final_parts(batch)
    source_job = await db.get(ProcessingJob, batch.source_job_id) if batch.source_job_id else None
    jobs = {job.split_part_index: job for job in await split_service.part_jobs(db, batch.id)}
    parts = []
    for part in final.get("parts", []):
        job = jobs.get(part.get("index"))
        parts.append(SplitPartInfo(
            index=part.get("index"),
            pages=part.get("pages", []),
            job_id=job.id if job else None,
            job_status=job.status if job else None,
            document_id=job.document_id if job else None,
            error_message=job.error_message if job else None,
            error_code=job.error_code if job else None,
        ))
    undo_reason = (
        await split_service.undo_blocker(db, batch)
        if batch.status in (SplitBatchStatus.DONE, SplitBatchStatus.DONE_WITH_ERRORS) else None
    )
    can_undo = batch.status in (SplitBatchStatus.DONE, SplitBatchStatus.DONE_WITH_ERRORS) and undo_reason is None
    blank_kinds = proposal.get("blank_kinds") or {}
    pages = []
    for raw in split_service.load_pages(batch):
        sig = raw.get("sig") or {}
        pages.append(SplitPage(
            n=raw.get("n"), w=raw.get("w", 0.0), h=raw.get("h", 0.0), rot=raw.get("rot", 0),
            blank=bool(raw.get("blank")), ink=raw.get("ink", 0.0), source=raw.get("source", "none"),
            conf=raw.get("conf", 0.0), counter=sig.get("counter"),
            blank_kind=blank_kinds.get(str(raw.get("n"))) if raw.get("blank") else None,
        ))
    return SplitBatchResponse(
        id=batch.id,
        origin=batch.origin,
        status=batch.status,
        version=batch.version,
        original_filename=batch.original_filename,
        job_source=batch.job_source,
        source_job_id=batch.source_job_id,
        source_document_id=batch.source_document_id,
        page_count=batch.page_count,
        pages_done=batch.pages_done,
        pages=pages,
        boundaries=[
            SplitBoundary(**{k: v for k, v in b.items() if k in SplitBoundary.model_fields})
            for b in proposal.get("boundaries", [])
        ],
        duplex=bool(proposal.get("duplex")),
        duplex_conf=float(proposal.get("duplex_conf") or 0.0),
        partial_from=proposal.get("partial_from"),
        llm_used=batch.llm_used,
        llm_unavailable=bool(proposal.get("llm_unavailable")),
        excluded=final.get("excluded", []),
        parts=parts,
        error_message=batch.error_message,
        error_code=source_job.error_code if source_job else None,
        can_undo=can_undo,
        undo_blocked_reason=undo_reason,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        confirmed_at=batch.confirmed_at,
        finished_at=batch.finished_at,
    )


@router.get("/split", response_model=PaginatedSplitBatches)
async def list_batches(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PaginatedSplitBatches:
    """Stapel-Liste, neueste zuerst. ``status`` kommagetrennt (z.B. PROPOSED,DETECTING)."""
    query = select(SplitBatch)
    count_query = select(func.count(SplitBatch.id))
    statuses = parse_enum_list(status, SplitBatchStatus, "Stapel-Status")
    if statuses:
        query = query.where(SplitBatch.status.in_(statuses))
        count_query = count_query.where(SplitBatch.status.in_(statuses))
    total = (await db.execute(count_query)).scalar_one()
    rows = (await db.execute(
        query.order_by(SplitBatch.created_at.desc(), SplitBatch.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    items = []
    for b in rows:
        parts, uncertain = _proposal_counts(b)
        items.append(SplitBatchListItem(
            id=b.id, origin=b.origin, status=b.status, original_filename=b.original_filename,
            job_source=b.job_source, page_count=b.page_count, pages_done=b.pages_done,
            proposed_parts=parts, uncertain=uncertain, created_at=b.created_at,
        ))
    return PaginatedSplitBatches(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/split/{batch_id}", response_model=SplitBatchResponse)
async def get_batch(
    batch_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SplitBatchResponse:
    batch = await split_service.get_batch(db, batch_id)
    return await build_batch_response(db, batch)


def _page_image(batch_id: str, page: int, settings: Settings, *, preview: bool) -> FileResponse:
    path = split_service.page_image_path(settings, batch_id, page, preview=preview)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Vorschaubild nicht gefunden")
    return FileResponse(path=path, media_type="image/jpeg" if preview else "image/png")


@router.get("/split/{batch_id}/pages/{page}/thumbnail")
async def get_page_thumbnail(
    batch_id: str, page: int, settings: Settings = Depends(get_settings),
) -> FileResponse:
    """200 px breites PNG der Seite (Raster im Editor)."""
    return _page_image(batch_id, page, settings, preview=False)


@router.get("/split/{batch_id}/pages/{page}/preview")
async def get_page_preview(
    batch_id: str, page: int, settings: Settings = Depends(get_settings),
) -> FileResponse:
    """1000 px breites JPEG der Seite (Grossansicht)."""
    return _page_image(batch_id, page, settings, preview=True)


@router.post("/split/{batch_id}/confirm", status_code=202, response_model=SplitConfirmResponse)
async def confirm_split(
    batch_id: str,
    body: SplitConfirmRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> SplitConfirmResponse:
    """Aufteilung bestaetigen -> Teil-Jobs (laufen danach durch die normale Pipeline)."""
    job_ids = await split_service.confirm(
        db, batch_id,
        version=body.version,
        parts=[part.pages for part in body.parts],
        excluded=body.excluded,
        settings=settings,
    )
    return SplitConfirmResponse(batch_id=batch_id, job_ids=job_ids)


@router.post("/split/{batch_id}/keep-whole", response_model=SplitBatchResponse)
async def keep_whole(
    batch_id: str,
    body: SplitVersionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> SplitBatchResponse:
    await split_service.keep_whole(db, batch_id, version=body.version, settings=settings)
    return await build_batch_response(db, await split_service.get_batch(db, batch_id))


@router.post("/split/{batch_id}/discard", response_model=SplitBatchResponse)
async def discard(
    batch_id: str,
    body: SplitVersionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> SplitBatchResponse:
    await split_service.discard(db, batch_id, version=body.version, settings=settings)
    return await build_batch_response(db, await split_service.get_batch(db, batch_id))


@router.post("/split/{batch_id}/undo", response_model=SplitUndoResponse)
async def undo_split(
    batch_id: str,
    body: SplitVersionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory=Depends(get_session_factory),
) -> SplitUndoResponse:
    """"Aufteilung rückgängig": Teile soft-deleted; MANUAL -> Original wieder
    aktiv (UNDONE), AUTO -> Stapel wieder als Vorschlag (PROPOSED). 409 mit
    Begruendung, wenn ein Teil schon bearbeitet wurde oder die Quelle fehlt."""
    result = await split_service.undo(db, batch_id, version=body.version, settings=settings)
    # Vektoren nach dem Commit (best-effort, nach der Response)
    background_tasks.add_task(split_service.finish_undo, result, settings, session_factory)
    batch = await split_service.get_batch(db, batch_id)
    return SplitUndoResponse(
        batch_id=batch_id,
        status=batch.status,
        removed_document_ids=result.removed_document_ids,
        restored_document_id=result.restored_document_id,
    )


@router.post("/split/{batch_id}/retry-failed", response_model=SplitRetryResponse)
async def retry_failed(
    batch_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SplitRetryResponse:
    job_ids = await split_service.retry_failed_parts(db, batch_id)
    return SplitRetryResponse(batch_id=batch_id, job_ids=job_ids)


@router.post("/documents/{document_id}/split", status_code=202, response_model=SplitRequestResponse)
async def request_document_split(
    document_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> SplitRequestResponse:
    """"Dokument aufteilen": legt einen Stapel-Job an; die Seiten erkennt der
    Queue-Worker, danach oeffnet der Editor den Stapel."""
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    batch, job = await split_service.request_document_split(db, document, settings)
    return SplitRequestResponse(batch_id=batch.id, job_id=job.id)
