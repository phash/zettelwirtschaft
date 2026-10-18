import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


class JobSource(str, enum.Enum):
    UPLOAD = "UPLOAD"
    WATCH_FOLDER = "WATCH_FOLDER"
    EMAIL = "EMAIL"
    # Stapel-Job aus "Dokument aufteilen" an einem archivierten Dokument.
    SPLIT_REQUEST = "SPLIT_REQUEST"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    # Stapel-PDF wartet auf die Bestaetigung der Aufteilung (Editor).
    # native_enum=False -> VARCHAR ohne CHECK, die laengeren Werte brauchen
    # keine DDL (SQLite erzwingt die VARCHAR-Laenge nicht).
    AWAITING_SPLIT = "AWAITING_SPLIT"
    # Stapel-Job wurde aufgeteilt (terminal, document_id bleibt NULL).
    SPLIT = "SPLIT"


# Maschinenlesbare Fehlerursache auf FAILED-Jobs (error_code).
JOB_ERROR_DUPLICATE = "DUPLICATE"
# Stapeldatei fehlt beim Bestaetigen der Aufteilung (Stapel bleibt PROPOSED).
JOB_ERROR_SPLIT_SOURCE_MISSING = "SPLIT_SOURCE_MISSING"


class SplitRole(str, enum.Enum):
    SOURCE = "SOURCE"  # Stapel-Job (die ganze PDF)
    PART = "PART"  # ein Teil nach der Aufteilung


class SplitMode(str, enum.Enum):
    AUTO = "AUTO"  # automatische Pruefung (ab Etappe 2 aktiv)
    FORCE = "FORCE"  # Upload-Option bzw. "Dokument aufteilen": immer in den Editor
    OFF = "OFF"  # nie pruefen (Teil-Jobs, "Als ein Dokument behalten")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        # Macht das Anlegen der Teil-Jobs idempotent (Recovery nach Absturz
        # zwischen Teildateien und Teil-Jobs, Doppelklick).
        Index(
            "ux_processing_jobs_split_part",
            "split_batch_id",
            "split_part_index",
            unique=True,
            sqlite_where=text("split_role = 'PART'"),
        ),
    )

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
    # Aufteilung (Spezifikation 12): Stapel- und Teil-Jobs verweisen auf ihren
    # Stapel. Nur diese Richtung hat einen FK (kein Zyklus mit split_batches).
    split_batch_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("split_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    split_role: Mapped[str | None] = mapped_column(String(10), nullable=True)  # SplitRole
    split_part_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-basiert
    # Nur Anzeige — massgeblich ist SplitBatch.final_parts.
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    split_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SplitMode.AUTO.value, server_default=SplitMode.AUTO.value
    )
    # JSON [{page, text, confidence, source}] — Seitentext aus der Erkennung.
    # Liegt waehrend der Erkennung am Stapel-Job, wird beim Bestaetigen auf die
    # Teil-Jobs verteilt und nach der Archivierung eines Teils geleert (der Text
    # steht dann in Document.ocr_text) — keine dauerhafte zweite Textkopie. Am
    # AUTO-Stapel-Job bleibt er bis zum Ende der Aufbewahrungsfrist (fuer
    # "Aufteilung rückgängig" + erneutes Bestaetigen ohne zweite OCR).
    page_ocr: Mapped[str | None] = mapped_column(Text, nullable=True)
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
