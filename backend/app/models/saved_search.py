import uuid
from datetime import datetime

from sqlalchemy import String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.database import Base


class SavedSearch(Base):
    __tablename__ = "saved_searches"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    query_params: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
