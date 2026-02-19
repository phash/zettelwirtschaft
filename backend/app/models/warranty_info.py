import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    warranty_type: Mapped[str] = mapped_column(
        Enum(WarrantyType, native_enum=False), nullable=False, default=WarrantyType.LEGAL
    )
    warranty_duration_months: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    retailer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="warranty_info")

    @property
    def is_expired(self) -> bool:
        return date.today() > self.warranty_end_date


from app.models.document import Document  # noqa: E402
