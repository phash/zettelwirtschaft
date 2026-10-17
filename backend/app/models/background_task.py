import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Enum, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


ACTIVE_TASK_STATUSES = (TaskStatus.PENDING, TaskStatus.RUNNING)


class BackgroundTask(Base):
    """Langlaufender Vorgang (Re-Analyse, E-Mail-Abruf, Voll-Backup ...).

    Wird von ``app.services.task_service.start_task`` angelegt und im
    Event-Loop der App ausgefuehrt; das Frontend pollt ``GET /api/tasks/{id}``.
    Laeuft ausschliesslich in-process — nach einem Neustart gibt es keinen
    Besitzer mehr, daher setzt ``recover_interrupted_tasks`` beim Start alle
    PENDING/RUNNING-Eintraege auf FAILED.
    """

    __tablename__ = "background_tasks"
    __table_args__ = (
        # Concurrency-Guard: hoechstens ein aktiver Vorgang pro (kind, key).
        # Partial Unique Index als DB-seitige Absicherung zum Lock in
        # task_service (NULL-Keys sind in SQLite-Unique-Indizes ohnehin distinct).
        Index(
            "ux_background_tasks_active_kind_key",
            "kind",
            "key",
            unique=True,
            sqlite_where=text("status IN ('PENDING', 'RUNNING') AND key IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    # Art des Vorgangs, z.B. "reanalyze", "email_fetch", "backup".
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Bezugsobjekt (Dokument-ID, E-Mail-Konto-ID ...) — Grundlage fuer den
    # Concurrency-Guard und fuer "laeuft fuer X gerade etwas?"-Abfragen.
    key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(TaskStatus, native_enum=False),
        nullable=False,
        default=TaskStatus.PENDING,
        index=True,
    )
    progress_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
