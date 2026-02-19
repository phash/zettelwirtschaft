from datetime import datetime

from pydantic import BaseModel

from app.models.processing_job import JobSource, JobStatus


class UploadResponse(BaseModel):
    document_id: str
    original_filename: str
    status: JobStatus
    message: str


class MultiUploadResponse(BaseModel):
    uploaded: list[UploadResponse]
    rejected: list[dict]


class JobStatusResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    original_filename: str
    stored_filename: str
    file_path: str
    file_type: str
    file_size_bytes: int
    source: JobSource
    status: JobStatus
    error_message: str | None = None
    retry_count: int
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    analysis_result: str | None = None
    created_at: datetime
    updated_at: datetime


class PaginatedJobsResponse(BaseModel):
    items: list[JobStatusResponse]
    total: int
    page: int
    page_size: int
