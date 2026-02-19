import re
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def generate_slug(name: str) -> str:
    """Erzeugt einen URL-freundlichen Slug aus einem Namen."""
    slug = name.lower()
    replacements = {
        "ae": "ae", "oe": "oe", "ue": "ue", "ss": "ss",
        "\u00e4": "ae", "\u00f6": "oe", "\u00fc": "ue", "\u00df": "ss",
    }
    for char, repl in replacements.items():
        slug = slug.replace(char, repl)
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug


class FilingScope(Base):
    __tablename__ = "filing_scopes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
