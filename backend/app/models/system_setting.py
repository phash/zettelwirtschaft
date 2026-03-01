from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemSetting(Base):
    """Persistente System-Einstellungen (Key-Value-Store in SQLite)."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
