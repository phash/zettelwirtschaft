import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.processing_job import JobSource, ProcessingJob
from app.schemas.processing_job import (
    JobStatusResponse,
    MultiUploadResponse,
    UploadResponse,
)
from app.services.file_validation_service import FileValidationError
from app.services.upload_service import process_upload

logger = logging.getLogger("zettelwirtschaft.api.documents")

router = APIRouter()


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
            # Datei in temporaeres Verzeichnis schreiben
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
                uploaded.append(
                    UploadResponse(
                        document_id=job.id,
                        original_filename=job.original_filename,
                        status=job.status,
                        message="Dokument wurde zum Verarbeiten eingereicht.",
                    )
                )
            finally:
                # Temp-Datei aufraeumen (wurde nach UPLOAD_DIR kopiert)
                tmp_path.unlink(missing_ok=True)

        except FileValidationError as e:
            rejected.append({"filename": e.filename or file.filename, "error": e.message})

        except Exception:
            logger.exception("Fehler beim Upload von %s", file.filename)
            rejected.append({"filename": file.filename, "error": "Interner Fehler beim Upload"})

    return MultiUploadResponse(uploaded=uploaded, rejected=rejected)


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
