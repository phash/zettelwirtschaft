"""Neu-Analyse mit Aenderungsvorschlag (Issue #39).

- ``ReanalysisProposal``: gespeicherter Vorschlag zu genau einem Dokument
  (hoechstens einer; ein neuer ersetzt den alten). Das Analyseergebnis liegt
  verlustfrei als ``AnalysisResult.to_dict()`` vor.
- ``ReanalysisRun`` / ``ReanalysisRunItem``: Sammellauf ueber viele Dokumente.
  Hoechstens ein aktiver Lauf (Partial Unique Index auf einer Konstanten).
  ``ReanalysisRunItem.document_id`` und ``ReanalysisProposal.run_id`` sind
  bewusst OHNE FK — Leser muessen fehlende Datensaetze tolerieren.
"""

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_AI = "WAITING_AI"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


ACTIVE_RUN_STATUSES = (RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.WAITING_AI)


class RunItemStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROPOSED = "PROPOSED"
    UNCHANGED = "UNCHANGED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ReanalysisProposal(Base):
    __tablename__ = "reanalysis_proposals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    with_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # AnalysisResult.to_dict() (inkl. Tags, Garantie, Rueckfragen, Scope).
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "discarded_worse": neue OCR war schlechter und wurde verworfen.
    ocr_note: Mapped[str | None] = mapped_column(String(40), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow
    )

    def analysis_result(self):
        """Rekonstruiert das ``AnalysisResult``-Dataclass aus dem JSON."""
        from app.services.analysis_service import AnalysisResult

        return AnalysisResult.from_dict(self.analysis)


class ReanalysisRun(Base):
    __tablename__ = "reanalysis_runs"
    __table_args__ = (
        # Hoechstens ein aktiver Lauf: konstanter Ausdruck + WHERE aktiv.
        Index(
            "ux_reanalysis_runs_active",
            text("(1)"),
            unique=True,
            sqlite_where=text("status IN ('RUNNING', 'PAUSED', 'WAITING_AI')"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    status: Mapped[str] = mapped_column(
        Enum(RunStatus, native_enum=False), nullable=False, default=RunStatus.RUNNING
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    with_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReanalysisRunItem(Base):
    __tablename__ = "reanalysis_run_items"
    __table_args__ = (
        Index("ix_reanalysis_run_items_run_status_pos", "run_id", "status", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reanalysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    # Bewusst ohne FK: Dokumente koennen waehrend des Laufs verschwinden.
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(RunItemStatus, native_enum=False), nullable=False, default=RunItemStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
