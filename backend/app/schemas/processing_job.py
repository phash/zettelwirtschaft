from datetime import datetime

from pydantic import BaseModel

from app.models.processing_job import JobSource, JobStatus


class UploadResponse(BaseModel):
    # ID des Verarbeitungs-Jobs (NICHT des spaeteren Dokuments) — das Dokument
    # entsteht erst bei der Archivierung, seine ID liefert JobStatusResponse.document_id.
    job_id: str
    original_filename: str
    status: JobStatus
    message: str


class MultiUploadResponse(BaseModel):
    uploaded: list[UploadResponse]
    rejected: list[dict]


class JobListItem(BaseModel):
    """Schlanke Job-Darstellung fuer Listen (Dashboard pollt alle 3 s) — ohne
    OCR-Volltext und Analyse-JSON."""

    model_config = {"from_attributes": True}

    id: str
    original_filename: str
    # NEW-001: stored_filename und file_path raus — gleiche Begruendung wie
    # VULN-014 fuer DocumentResponse (Pfad-Disclosure, hilft Path-Traversal).
    file_type: str
    file_size_bytes: int
    source: JobSource
    status: JobStatus
    error_message: str | None = None
    # Maschinenlesbare Fehlerursache, z.B. "DUPLICATE" (Datei bereits archiviert).
    error_code: str | None = None
    retry_count: int
    # Erzeugtes Dokument (ab Archivierung: COMPLETED/NEEDS_REVIEW) — Ziel fuer
    # den "Anzeigen"/"Prüfen"-Link.
    document_id: str | None = None
    created_at: datetime
    updated_at: datetime


class JobStatusResponse(JobListItem):
    """Vollstaendiger Job-Status eines einzelnen Jobs inkl. OCR-/Analyse-Ergebnis."""

    ocr_text: str | None = None
    ocr_confidence: float | None = None
    analysis_result: str | None = None


class PaginatedJobsResponse(BaseModel):
    items: list[JobListItem]
    total: int
    page: int
    page_size: int
