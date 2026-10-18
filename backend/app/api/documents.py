import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import raiseload

from app.config import Settings, get_settings
from app.core.file_utils import mime_type_for
from app.core.query_params import parse_document_types
from app.database import get_db, get_session_factory
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentTag, ReviewStatus, Tag
from app.models.filing_scope import FilingScope
from app.models.processing_job import JobSource, JobStatus, ProcessingJob, SplitMode
from app.models.split_batch import SplitBatch
from app.models.warranty_info import WarrantyInfo, select_active_document_warranties
from app.schemas.document import (
    DashboardStats,
    DocumentListItem,
    DocumentResponse,
    DocumentUpdate,
    PaginatedDocumentsResponse,
    TagCreate,
    TagResponse,
)
from app.schemas.processing_job import (
    JobStatusResponse,
    MultiUploadResponse,
    UploadResponse,
)
from app.services.file_relocation import RelocationError, relocate_document_file
from app.services.file_validation_service import FileValidationError
from app.services.search_service import (
    FTS_DOCUMENT_FIELDS,
    reindex_document_fts,
    remove_document_from_index,
)
from app.services.tax_year import ensure_tax_year
from app.services.vectorize_service import METADATA_CHUNK_FIELDS
from app.services.upload_service import discard_upload, process_upload_stream

logger = logging.getLogger("zettelwirtschaft.api.documents")

router = APIRouter()

_NOT_FOUND = "Dokument nicht gefunden"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


async def _get_active_document_or_404(db: AsyncSession, document_id: str) -> Document:
    """Laedt ein nicht geloeschtes Dokument. Soft-geloeschte Dokumente sind ueber
    die API nicht mehr erreichbar (Lesen, Bearbeiten, Datei, Tags)."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.status != DocumentStatus.DELETED,
        )
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return document


async def _document_response(db: AsyncSession, document: Document) -> DocumentResponse:
    """DocumentResponse inkl. Herkunft aus einem aufgeteilten Stapel (Teil x
    von y aus „datei.pdf“). Ein fehlender Stapel wird toleriert."""
    response = DocumentResponse.model_validate(document)
    if document.split_batch_id:
        batch = await db.get(SplitBatch, document.split_batch_id)
        if batch is not None:
            from app.services.split_service import load_final_parts

            response.split_part_count = len(load_final_parts(batch).get("parts", [])) or None
            response.split_source_filename = batch.original_filename
            if document.status != DocumentStatus.DELETED:
                from app.services.split_service import undo_blocker

                reason = await undo_blocker(db, batch)
                response.can_undo_split = reason is None
                response.split_undo_blocked_reason = reason
    return response


def _schedule_revectorize(
    background_tasks: BackgroundTasks,
    document_id: str,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Re-Vektorisierung nach dem Commit (BackgroundTask laeuft nach der
    Response; laedt das Dokument in eigener Session, best-effort)."""
    from app.services.vectorize_service import revectorize_document_safe

    background_tasks.add_task(revectorize_document_safe, document_id, settings, session_factory)


# === Upload ===


@router.post("/documents/upload", response_model=MultiUploadResponse)
async def upload_documents(
    files: list[UploadFile],
    split_check: bool = Form(False),
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> MultiUploadResponse:
    """Akzeptiert eine oder mehrere Dateien per Multipart-Upload.

    Jede Datei wird direkt in UPLOAD_DIR gestreamt (keine Zwischenkopie, kein
    vollstaendiges Einlesen in den RAM) und dort validiert.

    ``split_check=true`` ("Enthält mehrere Dokumente"): PDFs ab zwei Seiten
    gehen vor der Analyse in den Aufteilungs-Editor (Spezifikation 12).
    """
    uploaded: list[UploadResponse] = []
    rejected: list[dict] = []

    for file in files:
        if not file.filename:
            rejected.append({"filename": "(unbekannt)", "error": "Kein Dateiname"})
            continue

        job = None
        try:
            job = await process_upload_stream(
                fileobj=file.file,
                original_name=file.filename,
                declared_size=file.size,
                source=JobSource.UPLOAD,
                settings=settings,
                db=db,
                split_mode=SplitMode.FORCE if split_check else SplitMode.AUTO,
            )
            # Pro Datei comitten — sonst rollt ein spaeterer Fehler in der
            # gleichen Session alle vorherigen Uploads zurueck (H-19).
            await db.commit()
            uploaded.append(
                UploadResponse(
                    job_id=job.id,
                    original_filename=job.original_filename,
                    status=job.status,
                    message="Dokument wurde zum Verarbeiten eingereicht.",
                )
            )
        except FileValidationError as e:
            await db.rollback()
            rejected.append({"filename": e.filename or file.filename, "error": e.message})
        except Exception:
            logger.exception("Fehler beim Upload von %s", file.filename)
            await db.rollback()
            if job is not None:
                discard_upload(job)
            rejected.append({"filename": file.filename, "error": "Interner Fehler beim Upload"})

    return MultiUploadResponse(uploaded=uploaded, rejected=rejected)


# === Job Status ===


@router.get("/documents/{job_id}/status", response_model=JobStatusResponse)
async def get_document_status(
    job_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> JobStatusResponse:
    """Verarbeitungsstatus eines Upload-Jobs (ID aus UploadResponse.job_id).

    Sobald archiviert, verweist ``document_id`` auf das erzeugte Dokument.
    """
    result = await db.execute(
        select(ProcessingJob).where(ProcessingJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Verarbeitungsauftrag nicht gefunden")
    response = JobStatusResponse.model_validate(job)
    if job.status == JobStatus.PROCESSING and job.split_batch_id:
        from app.api.jobs import with_split_progress

        response.split_progress = (await with_split_progress(db, [job]))[0].split_progress
    return response


# === Document CRUD ===


@router.get("/documents", response_model=PaginatedDocumentsResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    document_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tax_relevant: bool | None = None,
    status: DocumentStatus | None = None,
    filing_scope_id: str | None = None,
    sort_by: str = Query(default="created_at", pattern="^(created_at|document_date|title|amount|document_type)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PaginatedDocumentsResponse:
    """Listet Dokumente mit Filtern und Paginierung.

    Ungueltige Filterwerte (Datum, Status, Dokumenttyp) ergeben 422 statt still
    keine Treffer.
    """
    query = select(Document).where(Document.status != DocumentStatus.DELETED)

    # Filter
    types = parse_document_types(document_type)
    if types:
        query = query.where(Document.document_type.in_(types))
    if date_from:
        query = query.where(Document.document_date >= date_from)
    if date_to:
        query = query.where(Document.document_date <= date_to)
    if tax_relevant is not None:
        query = query.where(Document.tax_relevant == tax_relevant)
    if status:
        query = query.where(Document.status == status)
    if filing_scope_id:
        query = query.where(Document.filing_scope_id == filing_scope_id)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Sorting — Document.id als Tie-Breaker: ohne eindeutige Ordnung liefert
    # SQLite gleichrangige Zeilen seitenuebergreifend doppelt oder gar nicht.
    sort_column = getattr(Document, sort_by, Document.created_at)
    ordered = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    query = query.order_by(ordered.nulls_last(), Document.id)

    # Pagination
    query = query.offset((page - 1) * page_size).limit(page_size)

    # M-26: nur tags + filing_scope laden (selectin per Default), warranty +
    # review_questions ueberspringen — DocumentListItem nutzt sie nicht.
    # raiseload statt noload (in SQLAlchemy 2.1 deprecated): ein versehentlicher
    # Zugriff faellt laut auf, statt still None/[] zu liefern.
    query = query.options(
        raiseload(Document.warranty_info),
        raiseload(Document.review_questions),
    )

    result = await db.execute(query)
    documents = result.scalars().all()

    return PaginatedDocumentsResponse(
        items=[DocumentListItem.model_validate(doc) for doc in documents],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> DocumentResponse:
    """Gibt ein einzelnes Dokument mit allen Metadaten zurueck."""
    document = await _get_active_document_or_404(db, document_id)
    return await _document_response(db, document)


@router.patch("/documents/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: str,
    update: DocumentUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> DocumentResponse:
    """Aktualisiert Metadaten eines Dokuments manuell."""
    document = await _get_active_document_or_404(db, document_id)
    values = update.model_dump(exclude_unset=True)

    new_scope: FilingScope | None = None
    if values.get("filing_scope_id") is not None:
        new_scope = await db.get(FilingScope, values["filing_scope_id"])
        if new_scope is None:
            raise HTTPException(status_code=422, detail="Ablagebereich nicht gefunden")

    changes = {}
    for field, value in values.items():
        old_value = getattr(document, field)
        if old_value != value:
            changes[field] = {"old": str(old_value), "new": str(value)}
            setattr(document, field, value)

    if not changes:
        return await _document_response(db, document)

    # Steuerrelevant ohne Jahr -> Jahr ableiten (abgeleitet, keine Benutzer-
    # Eingabe: bewusst nicht im AuditLog, damit eine Re-Analyse es aendern darf).
    ensure_tax_year(document)

    db.add(AuditLog(
        document_id=document.id,
        action=AuditAction.UPDATED,
        details=json.dumps(changes, ensure_ascii=False),
    ))

    if FTS_DOCUMENT_FIELDS & changes.keys():
        await reindex_document_fts(db, document.id)

    # Flush + Refresh: das UPDATE expired server-seitig berechnete Spalten
    # (updated_at via onupdate=func.now()). Ohne Refresh wuerde die
    # Response-Validierung sie lazy nachladen -> MissingGreenlet im
    # async-Kontext (seit SQLAlchemy 2.1 autoflusht auch text()).
    await db.flush()
    await db.refresh(document)
    # Commit VOR der Re-Vektorisierung (die eine eigene Session nutzt).
    scope_changed = "filing_scope_id" in changes
    if scope_changed and new_scope is not None:
        # Datei wandert in den Ordner des neuen Bereichs (copy -> commit -> delete),
        # wie bei der Review-Antwort und der Re-Analyse.
        try:
            await relocate_document_file(db, document, new_scope, settings.ARCHIVE_DIR)
        except RelocationError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        # file_path-UPDATE expired updated_at erneut (s.o.)
        await db.refresh(document)
    else:
        await db.commit()

    if METADATA_CHUNK_FIELDS & changes.keys():
        _schedule_revectorize(background_tasks, document.id, settings, session_factory)
    elif scope_changed:
        from app.services.vectorize_service import update_scope_metadata

        background_tasks.add_task(update_scope_metadata, [document.id], document.filing_scope_id, settings)

    return await _document_response(db, document)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
):
    """Soft-Delete: Setzt Status auf DELETED. Antwort: 204 No Content (M-23).

    Idempotent: ein bereits geloeschtes Dokument liefert erneut 204, ohne einen
    weiteren Audit-Eintrag. Es gibt keinen Papierkorb — das Dokument ist danach
    ueber die API nicht mehr erreichbar; derselbe Upload legt ein neues an.
    """
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if document.status == DocumentStatus.DELETED:
        return Response(status_code=204)

    document.status = DocumentStatus.DELETED
    db.add(AuditLog(
        document_id=document.id,
        action=AuditAction.DELETED,
    ))

    # FTS-Index bereinigen
    await remove_document_from_index(db, document_id)

    # R-02 (Re-Review): DB-Commit zuerst, DANN Vektoren loeschen. Sonst
    # Inkonsistenz: Vektoren weg, Document noch ACTIVE wenn der Commit fehlschlaegt.
    await db.commit()

    # Vektoren best-effort post-commit (orphan-Vektoren sind tolerierbar,
    # `rag_service` filtert sie via DB-Join sowieso aus).
    try:
        from app.services.vectorize_service import delete_document_vectors
        await delete_document_vectors(document_id, settings)
    except Exception:
        logger.warning("Vektoren konnten nicht geloescht werden fuer %s", document_id)

    return Response(status_code=204)


@router.get("/documents/{document_id}/file")
async def download_document_file(
    document_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Gibt die Originaldatei eines Dokuments zurueck (inline fuer Vorschau)."""
    document = await _get_active_document_or_404(db, document_id)

    file_path = Path(document.file_path).resolve()
    # Defense in depth: nur Dateien innerhalb des Archivs ausliefern.
    if not file_path.is_relative_to(Path(settings.ARCHIVE_DIR).resolve()) or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")

    return FileResponse(
        path=file_path,
        filename=document.original_filename,
        media_type=mime_type_for(file_path.suffix),
        content_disposition_type="inline",
    )


def _job_thumbnail_path(job_id: str, settings: Settings) -> Path | None:
    """Thumbnail eines noch nicht archivierten Jobs (THUMBNAIL_DIR/<job-id>.png).

    Die ID kommt roh aus dem URL-Pfad: nur echte UUIDs zulassen und den
    aufgeloesten Pfad auf THUMBNAIL_DIR beschraenken — sonst liessen sich unter
    Windows mit ``..\\`` oder ``C:\\...`` beliebige .png-Dateien abrufen.
    """
    if not _UUID_RE.match(job_id):
        return None
    thumbnail_dir = Path(settings.THUMBNAIL_DIR).resolve()
    candidate = (thumbnail_dir / f"{job_id}.png").resolve()
    if not candidate.is_relative_to(thumbnail_dir):
        return None
    return candidate


@router.get("/documents/{document_id}/thumbnail")
async def get_document_thumbnail(
    document_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Gibt das Thumbnail eines Dokuments zurueck — oder, solange der Upload
    noch verarbeitet wird, das Thumbnail des Jobs (ID = Job-ID)."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if document is not None:
        if document.status == DocumentStatus.DELETED or not document.thumbnail_path:
            raise HTTPException(status_code=404, detail="Thumbnail nicht gefunden")
        thumb_path = Path(document.thumbnail_path)
    else:
        thumb_path = _job_thumbnail_path(document_id, settings)

    if thumb_path is None or not thumb_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail nicht gefunden")

    return FileResponse(path=thumb_path, media_type="image/png")


# === Tags ===


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[TagResponse]:
    """Listet alle Tags auf."""
    result = await db.execute(select(Tag).order_by(Tag.name))
    tags = result.scalars().all()
    return [TagResponse.model_validate(t) for t in tags]


@router.post("/documents/{document_id}/tags", response_model=DocumentResponse)
async def add_tag_to_document(
    document_id: str,
    tag_data: TagCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> DocumentResponse:
    """Fuegt einem Dokument einen Tag hinzu."""
    document = await _get_active_document_or_404(db, document_id)

    tag_name = tag_data.name.strip().lower()
    if not tag_name:
        raise HTTPException(status_code=422, detail="Tag-Name darf nicht leer sein")

    # Tag holen oder erstellen
    tag_result = await db.execute(select(Tag).where(Tag.name == tag_name))
    tag = tag_result.scalar_one_or_none()
    if not tag:
        tag = Tag(name=tag_name, is_auto_generated=False)
        db.add(tag)
        await db.flush()

    # Pruefen ob bereits zugewiesen — explizit per Junction-Table-Query
    # statt `tag in document.tags`, das in async-Kontext ggf. einen
    # MissingGreenlet-Refresh aufrufen wuerde.
    existing_link = await db.execute(
        select(DocumentTag).where(
            DocumentTag.document_id == document.id,
            DocumentTag.tag_id == tag.id,
        )
    )
    if existing_link.scalar_one_or_none() is None:
        db.add(DocumentTag(document_id=document.id, tag_id=tag.id))
        db.add(AuditLog(
            document_id=document.id,
            action=AuditAction.TAG_ADDED,
            details=json.dumps({"tag": tag_name}, ensure_ascii=False),
        ))
        await reindex_document_fts(db, document.id)
        # Tags fuer Response neu laden — Relationship cache war u.U. stale.
        await db.refresh(document, ["tags"])
        await db.commit()
        _schedule_revectorize(background_tasks, document.id, settings, session_factory)

    return await _document_response(db, document)


@router.delete("/documents/{document_id}/tags/{tag_id:int}")
async def remove_tag_from_document(
    document_id: str,
    tag_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> dict:
    """Entfernt einen Tag per ID; 404 wenn der Tag nicht existiert oder nicht
    zugewiesen ist (frueher: 200 "war nicht zugewiesen", die UI entfernte den
    Chip und nach dem Neuladen war er wieder da).

    Bewusst nur per ID: die fruehere Namens-Route scheiterte an '/', '#', '?'
    und '%' im Namen, und neben einer ID-Route waere ein rein numerischer
    Tag-Name ("2024") mehrdeutig.
    """
    document = await _get_active_document_or_404(db, document_id)
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag nicht gefunden")

    delete_result = await db.execute(
        delete(DocumentTag).where(
            DocumentTag.document_id == document.id,
            DocumentTag.tag_id == tag.id,
        )
    )
    if not delete_result.rowcount:
        raise HTTPException(status_code=404, detail="Tag ist diesem Dokument nicht zugewiesen")

    db.add(AuditLog(
        document_id=document.id,
        action=AuditAction.TAG_REMOVED,
        details=json.dumps({"tag": tag.name}, ensure_ascii=False),
    ))
    await reindex_document_fts(db, document.id)
    await db.commit()
    _schedule_revectorize(background_tasks, document.id, settings, session_factory)
    return {"message": f"Tag '{tag.name}' entfernt"}


# === Dashboard Stats ===


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> DashboardStats:
    """Gibt Dashboard-Statistiken zurueck."""
    # Total documents
    total_result = await db.execute(
        select(func.count(Document.id)).where(Document.status != DocumentStatus.DELETED)
    )
    total = total_result.scalar() or 0

    # This month — Monatsbeginn in lokaler Zeit (TIMEZONE), als UTC verglichen:
    # created_at wird in UTC gespeichert.
    local_now = datetime.now(settings.local_tz)
    month_start_utc = (
        local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    month_result = await db.execute(
        select(func.count(Document.id)).where(
            Document.status != DocumentStatus.DELETED,
            Document.created_at >= month_start_utc,
        )
    )
    this_month = month_result.scalar() or 0

    # Pending reviews
    review_result = await db.execute(
        select(func.count(Document.id)).where(
            Document.review_status == ReviewStatus.NEEDS_REVIEW,
            Document.status != DocumentStatus.DELETED,
        )
    )
    pending_reviews = review_result.scalar() or 0

    # Garantien, die in den naechsten 30 Tagen ablaufen — nur nicht geloeschte
    # Dokumente; Grenzen wie der "laeuft ab"-Filter der Garantie-Liste
    # (heute <= Enddatum <= heute + N, am Enddatum gilt sie noch).
    today = date.today()
    expiring_query = select_active_document_warranties().where(
        WarrantyInfo.warranty_end_date.between(today, today + timedelta(days=30))
    )
    expiring = (
        await db.execute(select(func.count()).select_from(expiring_query.subquery()))
    ).scalar() or 0

    # Processing jobs
    jobs_result = await db.execute(
        select(func.count(ProcessingJob.id)).where(
            ProcessingJob.status.in_([JobStatus.PENDING, JobStatus.PROCESSING])
        )
    )
    processing = jobs_result.scalar() or 0

    return DashboardStats(
        total_documents=total,
        documents_this_month=this_month,
        pending_reviews=pending_reviews,
        expiring_warranties_30d=expiring,
        processing_jobs=processing,
    )
