import json
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.config import Settings, get_settings
from app.database import get_db
from app.models.audit_log import AuditAction, AuditLog
from app.models.document import Document, DocumentStatus, DocumentType, DocumentTag, ReviewStatus, Tag
from app.models.processing_job import JobSource, JobStatus, ProcessingJob
from app.models.review_question import ReviewQuestion
from app.models.warranty_info import WarrantyInfo
from app.schemas.document import (
    DashboardStats,
    DocumentListItem,
    DocumentResponse,
    DocumentUpdate,
    PaginatedDocumentsResponse,
    ReviewQuestionAnswer,
    ReviewQuestionResponse,
    TagCreate,
    TagResponse,
)
from app.schemas.processing_job import (
    JobStatusResponse,
    MultiUploadResponse,
    UploadResponse,
)
from app.services.file_validation_service import FileValidationError
from app.services.upload_service import process_upload

logger = logging.getLogger("zettelwirtschaft.api.documents")

router = APIRouter()


# === Upload ===


@router.post("/documents/upload", response_model=MultiUploadResponse)
async def upload_documents(
    files: list[UploadFile],
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MultiUploadResponse:
    """Akzeptiert eine oder mehrere Dateien per Multipart-Upload."""
    uploaded: list[UploadResponse] = []
    rejected: list[dict] = []

    for file in files:
        if not file.filename:
            rejected.append({"filename": "(unbekannt)", "error": "Kein Dateiname"})
            continue

        try:
            content = await file.read()
            file_size = len(content)

            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)

            try:
                job = await process_upload(
                    file_path=tmp_path,
                    original_name=file.filename,
                    file_size=file_size,
                    source=JobSource.UPLOAD,
                    settings=settings,
                    db=db,
                )
                # Pro Datei comitten — sonst rollt ein spaeterer Fehler in der
                # gleichen Session alle vorherigen Uploads zurueck (H-19).
                await db.commit()
                uploaded.append(
                    UploadResponse(
                        document_id=job.id,
                        original_filename=job.original_filename,
                        status=job.status,
                        message="Dokument wurde zum Verarbeiten eingereicht.",
                    )
                )
            finally:
                tmp_path.unlink(missing_ok=True)

        except FileValidationError as e:
            await db.rollback()
            rejected.append({"filename": e.filename or file.filename, "error": e.message})
        except Exception:
            logger.exception("Fehler beim Upload von %s", file.filename)
            await db.rollback()
            rejected.append({"filename": file.filename, "error": "Interner Fehler beim Upload"})

    return MultiUploadResponse(uploaded=uploaded, rejected=rejected)


# === Job Status ===


@router.get("/documents/{document_id}/status", response_model=JobStatusResponse)
async def get_document_status(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    """Gibt den aktuellen Verarbeitungsstatus eines Dokuments zurueck."""
    result = await db.execute(
        select(ProcessingJob).where(ProcessingJob.id == document_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return JobStatusResponse.model_validate(job)


# === Document CRUD ===


@router.get("/documents", response_model=PaginatedDocumentsResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tax_relevant: bool | None = None,
    status: str | None = None,
    filing_scope_id: str | None = None,
    sort_by: str = Query(default="created_at", pattern="^(created_at|document_date|title|amount|document_type)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedDocumentsResponse:
    """Listet Dokumente mit Filtern und Paginierung."""
    query = select(Document).where(Document.status != DocumentStatus.DELETED)

    # Filter
    if document_type:
        types = [t.strip() for t in document_type.split(",")]
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

    # Sorting
    sort_column = getattr(Document, sort_by, Document.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Pagination
    query = query.offset((page - 1) * page_size).limit(page_size)

    # M-26: nur tags + filing_scope laden (selectin per Default), warranty +
    # review_questions ueberspringen — DocumentListItem nutzt sie nicht.
    query = query.options(
        noload(Document.warranty_info),
        noload(Document.review_questions),
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
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Gibt ein einzelnes Dokument mit allen Metadaten zurueck."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return DocumentResponse.model_validate(document)


@router.patch("/documents/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: str,
    update: DocumentUpdate,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Aktualisiert Metadaten eines Dokuments manuell."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    changes = {}
    for field, value in update.model_dump(exclude_unset=True).items():
        old_value = getattr(document, field)
        if old_value != value:
            changes[field] = {"old": str(old_value), "new": str(value)}
            setattr(document, field, value)

    if changes:
        audit = AuditLog(
            document_id=document.id,
            action=AuditAction.UPDATED,
            details=json.dumps(changes, ensure_ascii=False),
        )
        db.add(audit)

    fts_fields = {"title", "issuer", "summary"}
    if changes and fts_fields & set(changes.keys()):
        tags_str = " ".join(t.name for t in document.tags) if document.tags else ""
        await db.execute(text("DELETE FROM documents_fts WHERE doc_id = :doc_id"), {"doc_id": document_id})
        await db.execute(
            text("INSERT INTO documents_fts(doc_id, title, ocr_text, issuer, summary, tags) VALUES (:doc_id, :title, :ocr_text, :issuer, :summary, :tags)"),
            {"doc_id": document_id, "title": document.title or "", "ocr_text": document.ocr_text or "", "issuer": document.issuer or "", "summary": document.summary or "", "tags": tags_str},
        )

    return DocumentResponse.model_validate(document)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Soft-Delete: Setzt Status auf DELETED."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    document.status = DocumentStatus.DELETED
    audit = AuditLog(
        document_id=document.id,
        action=AuditAction.DELETED,
    )
    db.add(audit)

    # FTS-Index bereinigen
    await db.execute(text("DELETE FROM documents_fts WHERE doc_id = :doc_id"), {"doc_id": document_id})

    # Vektoren asynchron loeschen (non-blocking)
    try:
        from app.services.vectorize_service import delete_document_vectors
        settings = get_settings()
        await delete_document_vectors(document_id, settings)
    except Exception:
        logger.warning("Vektoren konnten nicht geloescht werden fuer %s", document_id)

    return {"message": "Dokument geloescht", "id": document_id}


@router.get("/documents/{document_id}/file")
async def download_document_file(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Gibt die Originaldatei eines Dokuments zurueck (inline fuer Vorschau)."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    file_path = Path(document.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")

    # MIME-Typ anhand Dateiendung bestimmen fuer inline-Vorschau
    mime_types = {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "tiff": "image/tiff",
        "tif": "image/tiff",
        "bmp": "image/bmp",
    }
    ext = file_path.suffix.lower().lstrip(".")
    media_type = mime_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=file_path,
        filename=document.original_filename,
        media_type=media_type,
        content_disposition_type="inline",
    )


@router.get("/documents/{document_id}/thumbnail")
async def get_document_thumbnail(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Gibt das Thumbnail eines Dokuments zurueck."""
    # Try Document first, then ProcessingJob
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if document and document.thumbnail_path:
        thumb_path = Path(document.thumbnail_path)
    else:
        thumb_path = Path(settings.THUMBNAIL_DIR) / f"{document_id}.png"

    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail nicht gefunden")

    return FileResponse(path=thumb_path, media_type="image/png")


# === Tags ===


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    db: AsyncSession = Depends(get_db),
) -> list[TagResponse]:
    """Listet alle Tags auf."""
    result = await db.execute(select(Tag).order_by(Tag.name))
    tags = result.scalars().all()
    return [TagResponse.model_validate(t) for t in tags]


@router.post("/documents/{document_id}/tags", response_model=DocumentResponse)
async def add_tag_to_document(
    document_id: str,
    tag_data: TagCreate,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Fuegt einem Dokument einen Tag hinzu."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    tag_name = tag_data.name.strip().lower()

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
        audit = AuditLog(
            document_id=document.id,
            action=AuditAction.TAG_ADDED,
            details=json.dumps({"tag": tag_name}, ensure_ascii=False),
        )
        db.add(audit)
        await db.flush()
        # Tags fuer Response neu laden — Relationship cache war u.U. stale.
        await db.refresh(document, ["tags"])

    return DocumentResponse.model_validate(document)


@router.delete("/documents/{document_id}/tags/{tag_name}")
async def remove_tag_from_document(
    document_id: str,
    tag_name: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Entfernt einen Tag von einem Dokument."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    tag_result = await db.execute(select(Tag).where(Tag.name == tag_name.lower()))
    tag = tag_result.scalar_one_or_none()
    if tag is None:
        return {"message": f"Tag '{tag_name}' war nicht zugewiesen"}

    from sqlalchemy import delete as sa_delete
    delete_result = await db.execute(
        sa_delete(DocumentTag).where(
            DocumentTag.document_id == document.id,
            DocumentTag.tag_id == tag.id,
        )
    )
    if delete_result.rowcount:
        audit = AuditLog(
            document_id=document.id,
            action=AuditAction.TAG_REMOVED,
            details=json.dumps({"tag": tag_name}, ensure_ascii=False),
        )
        db.add(audit)
        await db.flush()

    return {"message": f"Tag '{tag_name}' entfernt"}


# === Dashboard Stats ===


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
) -> DashboardStats:
    """Gibt Dashboard-Statistiken zurueck."""
    from datetime import date, timedelta

    # Total documents
    total_result = await db.execute(
        select(func.count(Document.id)).where(Document.status != DocumentStatus.DELETED)
    )
    total = total_result.scalar() or 0

    # This month
    today = date.today()
    first_of_month = today.replace(day=1)
    month_result = await db.execute(
        select(func.count(Document.id)).where(
            Document.status != DocumentStatus.DELETED,
            Document.created_at >= first_of_month.isoformat(),
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

    # Expiring warranties in 30 days
    thirty_days = today + timedelta(days=30)
    warranty_result = await db.execute(
        select(func.count(WarrantyInfo.id)).where(
            WarrantyInfo.warranty_end_date >= today.isoformat(),
            WarrantyInfo.warranty_end_date <= thirty_days.isoformat(),
        )
    )
    expiring = warranty_result.scalar() or 0

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
