"""Pydantic-Schemas fuer E-Mail-Konten und verarbeitete E-Mails."""

from datetime import datetime

from pydantic import BaseModel, Field


class EmailAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    imap_host: str = Field(..., min_length=1, max_length=500)
    imap_port: int = Field(default=993, ge=1, le=65535)
    use_ssl: bool = True
    username: str = Field(..., min_length=1, max_length=500)
    password: str = Field(..., min_length=1)
    folder_inbox: str = Field(default="INBOX", max_length=500)
    folder_processed: str = Field(default="Zettelwirtschaft/Verarbeitet", max_length=500)
    schedule_type: str = Field(default="MANUAL")
    cron_expression: str | None = None
    filing_scope_id: int | None = None


class EmailAccountUpdate(BaseModel):
    name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    use_ssl: bool | None = None
    username: str | None = None
    password: str | None = None
    folder_inbox: str | None = None
    folder_processed: str | None = None
    schedule_type: str | None = None
    cron_expression: str | None = None
    is_active: bool | None = None
    filing_scope_id: int | None = None


class EmailAccountResponse(BaseModel):
    id: int
    name: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    folder_inbox: str
    folder_processed: str
    schedule_type: str
    cron_expression: str | None
    is_active: bool
    last_checked_at: datetime | None
    last_error: str | None
    filing_scope_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailTestResult(BaseModel):
    success: bool
    error: str | None = None


class EmailFetchResult(BaseModel):
    total: int
    relevant: int
    irrelevant: int
    skipped: int
    failed: int


class ProcessedEmailResponse(BaseModel):
    id: int
    message_id: str
    subject: str | None
    sender: str | None
    received_at: datetime | None
    status: str
    relevance_reason: str | None
    processing_job_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EmailStatsResponse(BaseModel):
    account_id: int
    account_name: str
    total_processed: int
    relevant: int
    irrelevant: int
    failed: int
    last_checked_at: datetime | None
