import enum
from datetime import datetime

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


class ScheduleType(str, enum.Enum):
    CRON = "CRON"
    MANUAL = "MANUAL"
    IDLE = "IDLE"


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    imap_host: Mapped[str] = mapped_column(String(500), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[str] = mapped_column(String(500), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    folder_inbox: Mapped[str] = mapped_column(String(500), nullable=False, default="INBOX")
    folder_processed: Mapped[str] = mapped_column(
        String(500), nullable=False, default="Zettelwirtschaft/Verarbeitet"
    )
    schedule_type: Mapped[ScheduleType] = mapped_column(
        Enum(ScheduleType, native_enum=False), nullable=False, default=ScheduleType.MANUAL
    )
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # H-ARCH-3: Index fuer Reverse-Lookups + Konsistenz mit anderen
    # filing_scope_id-FKs.
    filing_scope_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("filing_scopes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )
