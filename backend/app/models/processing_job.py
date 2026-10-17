import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


class JobSource(str, enum.Enum):
    UPLOAD = "UPLOAD"
    WATCH_FOLDER = "WATCH_FOLDER"
    EMAIL = "EMAIL"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# Maschinenlesbare Fehlerursache auf FAILED-Jobs (error_code).
JOB_ERROR_DUPLICATE = "DUPLICATE"


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(
        Enum(JobSource, native_enum=False),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(JobStatus, native_enum=False),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Maschinenlesbare Ursache (z.B. JOB_ERROR_DUPLICATE) — das Frontend und der
    # Retry-Endpoint unterscheiden so Duplikate von echten Systemfehlern, ohne
    # den deutschen Fehlertext zu parsen.
    error_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # B5: Heartbeat (Claim + zwischen den Verarbeitungsphasen). Diagnose-
    # Information: Beim Start setzt queue_worker_service.recover_interrupted_jobs
    # JEDEN noch PROCESSING-Job zurueck — es gibt nur einen In-Process-Worker,
    # nach einem Neustart kann ihn also niemand mehr bearbeiten.
    processing_started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    # H-ARCH-4: Index fuer Reverse-Lookups (Welche Jobs gehoeren zu Konto X?).
    email_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Erzeugtes Dokument (gesetzt bei der Archivierung, COMPLETED/NEEDS_REVIEW).
    # Upload-/Dashboard-Ansicht verlinken darueber auf /dokumente/<id> — die
    # Job-ID ist nicht die Dokument-ID.
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        nullable=False,
        index=True,  # Migration 011 (Queue-Reihenfolge, Job-Liste)
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
