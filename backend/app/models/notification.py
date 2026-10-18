import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


class NotificationType(str, enum.Enum):
    WARRANTY_EXPIRING = "WARRANTY_EXPIRING"
    WARRANTY_EXPIRED = "WARRANTY_EXPIRED"
    # Vom Queue-Worker erzeugt (notification_service): Dokument braucht Review
    # bzw. Verarbeitung fehlgeschlagen. PROCESSING_DONE wurde entfernt — nie
    # erzeugt, also auch nie gespeichert (native_enum=False -> VARCHAR ohne
    # CHECK-Constraint, keine Migration noetig).
    REVIEW_NEEDED = "REVIEW_NEEDED"
    SYSTEM = "SYSTEM"
    # Stapel-PDF wartet auf die Aufteilung (document_id NULL, Frontend -> /pruefen).
    SPLIT_NEEDED = "SPLIT_NEEDED"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    type: Mapped[str] = mapped_column(
        Enum(NotificationType, native_enum=False), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
