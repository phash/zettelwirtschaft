import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailStatus(str, enum.Enum):
    RELEVANT = "RELEVANT"
    IRRELEVANT = "IRRELEVANT"
    FAILED = "FAILED"


class ProcessedEmail(Base):
    __tablename__ = "processed_emails"
    __table_args__ = (
        UniqueConstraint("email_account_id", "message_id", name="uq_account_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(1000), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(EmailStatus, native_enum=False), nullable=False
    )
    relevance_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # H-ARCH-4: Index fuer Reverse-Lookups.
    processing_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
