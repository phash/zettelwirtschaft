import enum
import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Date,
    Enum,
    ForeignKey,
    Integer,
    Select,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import UTCDateTime
from app.database import Base


class WarrantyType(str, enum.Enum):
    LEGAL = "LEGAL"
    MANUFACTURER = "MANUFACTURER"
    EXTENDED = "EXTENDED"


class WarrantyInfo(Base):
    __tablename__ = "warranty_info"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    product_category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    warranty_end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # validate_strings: ein unbekannter String wird schon beim Schreiben
    # abgelehnt — vorher landete z.B. 'FOO' in der DB und jedes Laden der
    # Zeile (Garantie-Liste, Dokument-Detail) warf LookupError -> 500.
    warranty_type: Mapped[str] = mapped_column(
        Enum(WarrantyType, native_enum=False, validate_strings=True),
        nullable=False,
        default=WarrantyType.LEGAL,
    )
    warranty_duration_months: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    retailer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reminder_90d_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reminder_30d_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reminder_0d_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    # H-18: war "selectin" — beidseitig eager fuehrte zu unnoetigen JOIN/SELECT-
    # Lawinen ueber tags + review_questions wenn /api/warranties die Garantien
    # listet. "raise" zwingt Code der das Document hier braucht, es per
    # explizitem selectinload(...) zu laden.
    document: Mapped["Document"] = relationship(back_populates="warranty_info", lazy="raise")

    @property
    def is_expired(self) -> bool:
        return is_expired_on(self.warranty_end_date, date.today())

    def rearm_reminders(self) -> None:
        """Setzt die Erinnerungs-Flags zurueck (nach Aenderung des Enddatums).

        Die Reminder-Logik entscheidet anhand des neuen Datums selbst, welche
        Erinnerung noch faellig ist — laengst vergangene Schwellen feuern nicht.
        """
        self.reminder_sent = False
        self.reminder_90d_sent = False
        self.reminder_30d_sent = False
        self.reminder_0d_sent = False


# --- Eine Definition der Garantie-Zustaende (Liste, Statistik, is_expired) ---
# abgelaufen := Enddatum < heute   (am Enddatum selbst gilt die Garantie noch)
# aktiv      := Enddatum >= heute
# laeuft ab  := heute <= Enddatum <= heute + 90 Tage
EXPIRING_SOON_DAYS = 90
WarrantyStatus = Literal["active", "expiring", "expired"]

# Prefix der Garantie-Aenderungen im AuditLog (UPDATED, PATCH /warranties).
# Die Re-Analyse erkennt daran, dass der Benutzer die Garantie bearbeitet hat,
# und laesst sie unangetastet.
WARRANTY_AUDIT_PREFIX = "warranty_info."


def is_expired_on(end_date: date, today: date) -> bool:
    return end_date < today


def warranty_status_condition(status: WarrantyStatus, today: date) -> ColumnElement[bool]:
    """SQL-Bedingung fuer ``active`` / ``expiring`` / ``expired``."""
    end = WarrantyInfo.warranty_end_date
    if status == "active":
        return end >= today
    if status == "expiring":
        return end.between(today, today + timedelta(days=EXPIRING_SOON_DAYS))
    if status == "expired":
        return end < today
    raise ValueError(f"Unbekannter Garantie-Status: {status}")


def select_active_document_warranties() -> Select:
    """Garantien, deren Dokument nicht geloescht ist (Liste, Stats, Reminder)."""
    return (
        select(WarrantyInfo)
        .join(Document, Document.id == WarrantyInfo.document_id)
        .where(Document.status != DocumentStatus.DELETED)
    )


from app.models.document import Document, DocumentStatus  # noqa: E402
