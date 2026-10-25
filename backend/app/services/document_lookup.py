"""Nicht geloeschte Dokumente laden — eine Definition fuer API und Dienste.

Soft-geloeschte Dokumente (Status ``DELETED``) sind fuer Lesen, Bearbeiten,
Pruefen und Neu-Analyse unsichtbar.
"""

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus


def not_deleted() -> ColumnElement[bool]:
    """WHERE-Bedingung: das Dokument ist nicht (soft-)geloescht."""
    return Document.status != DocumentStatus.DELETED


async def load_active_document(session: AsyncSession, document_id: str) -> Document | None:
    """Dokument laden — ``None`` fuer unbekannte UND geloeschte Dokumente."""
    result = await session.execute(
        select(Document).where(Document.id == document_id).where(not_deleted())
    )
    return result.scalar_one_or_none()
