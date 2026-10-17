import re
import uuid
from datetime import datetime

from sqlalchemy import Boolean, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_types import UTCDateTime
from app.core.text_utils import transliterate_ascii
from app.database import Base


def generate_slug(name: str) -> str:
    """Erzeugt einen URL-/Ordner-freundlichen Slug aus einem Namen.

    Der Slug wird Teil des Archivpfads (``archive/{slug}/...``) und darf daher
    nie leer sein: ein Name ohne ASCII-Buchstaben ('€', Emoji, CJK) bekommt
    einen generierten Slug ``bereich-<hex>`` (sonst zeigte der Scope-Ordner
    auf das Archiv-Wurzelverzeichnis).
    """
    slug = transliterate_ascii(name).lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        slug = f"bereich-{uuid.uuid4().hex[:8]}"
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
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    @property
    def parsed_keywords(self) -> list[str]:
        if not self.keywords:
            return []
        try:
            import json
            return json.loads(self.keywords)
        except (json.JSONDecodeError, TypeError):
            return []
