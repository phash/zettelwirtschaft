"""Stapel-PDF, das in einzelne Dokumente aufgeteilt wird (Spezifikation 12).

Ein Stapel entsteht aus einem Stapel-Job (``ProcessingJob.split_role='SOURCE'``):
automatische Pruefung (AUTO), Upload-Option "vor der Analyse aufteilen" (AUTO)
oder "Dokument aufteilen" an einem archivierten Dokument (MANUAL). Die Seiten-
merkmale und der Aufteilungsvorschlag liegen hier, nicht als JSON am Job —
``ReviewQuestion.document_id`` ist NOT NULL, ein Stapel hat aber noch kein
Dokument, und ein Blob am Job ginge beim Loeschen des Jobs verloren.

FK-Richtung: nur ``processing_jobs`` und ``documents`` zeigen auf diese Tabelle
(``split_batch_id``), nicht umgekehrt — sonst entstuende ein FK-Zyklus und
``create_fresh_schema`` (create_all) muesste Tabellen in zwei Schritten anlegen.
``source_job_id``/``source_document_id`` sind deshalb bewusst OHNE FK; jede
Stelle, die sie liest, muss mit einem fehlenden Datensatz umgehen koennen.
Dokumente werden nur soft-deleted, Jobs nur im terminalen Zustand geloescht.
WICHTIG: Wird jemals ein Hard-Delete fuer Dokumente eingefuehrt, muss er die
``split_batches`` (source_document_id) mitbereinigen.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


class SplitOrigin(str, enum.Enum):
    AUTO = "AUTO"  # automatische Erkennung oder Upload-Option
    MANUAL = "MANUAL"  # "Dokument aufteilen" an einem archivierten Dokument


class SplitBatchStatus(str, enum.Enum):
    DETECTING = "DETECTING"
    PROPOSED = "PROPOSED"
    SPLITTING = "SPLITTING"
    DONE = "DONE"
    DONE_WITH_ERRORS = "DONE_WITH_ERRORS"
    KEPT_WHOLE = "KEPT_WHOLE"
    DISCARDED = "DISCARDED"
    UNDONE = "UNDONE"
    FAILED = "FAILED"


# Stapel, deren Quelldatei noch gebraucht wird: kein Aufraeumer darf sie loeschen
# (split_service.protected_source_paths).
ACTIVE_BATCH_STATUSES = (
    SplitBatchStatus.DETECTING,
    SplitBatchStatus.PROPOSED,
    SplitBatchStatus.SPLITTING,
)

# Stapel, deren Datei-Hash einen erneuten Import als Duplikat markiert
# (bereits aufgeteilt oder wartet auf die Aufteilung).
DUPLICATE_BATCH_STATUSES = (
    SplitBatchStatus.PROPOSED,
    SplitBatchStatus.SPLITTING,
    SplitBatchStatus.DONE,
    SplitBatchStatus.DONE_WITH_ERRORS,
)


class SplitBatch(Base):
    __tablename__ = "split_batches"
    __table_args__ = (
        Index("ix_split_batches_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    origin: Mapped[str] = mapped_column(Enum(SplitOrigin, native_enum=False), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(SplitBatchStatus, native_enum=False),
        nullable=False,
        default=SplitBatchStatus.DETECTING,
        index=True,
    )
    # Optimistisches Locking: jeder Statuswechsel per
    # UPDATE ... WHERE status=:alt AND version=:v (split_service._transition).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Bewusst ohne FK (siehe Modul-Docstring).
    source_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Herkunft fuer die Teile (Ablagebereich des E-Mail-Kontos, JobSource).
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_source: Mapped[str] = mapped_column(String(20), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    # data/split/<id>/source.pdf
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    # SHA-256 der Stapeldatei — erkennt den erneuten Import eines aufgeteilten Stapels.
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Checkpoint der Erkennung (Seiten mit Merkmalen + Text).
    pages_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # JSON: Merkmale pro Seite (kein Volltext), siehe split_detection_service.
    pages: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: {boundaries:[...], duplex, duplex_conf, partial_from}
    proposal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: {parts:[{index, pages:[...]}], excluded:[...], parts_written}
    final_parts: Mapped[str | None] = mapped_column(Text, nullable=True)
    detector_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    detection_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
