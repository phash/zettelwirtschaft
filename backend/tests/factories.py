"""Gemeinsame Test-Factories fuer Datenbank-Objekte.

Ersetzt die frueher in jeder Testdatei kopierten ``_create_doc``-Helfer.
``file_hash`` ist per Default eine UUID: der fruehere
``f"hash_{id(overrides)}"``-Trick konnte kollidieren, weil CPython die id()
eines freigegebenen dicts wiederverwendet (``Document.file_hash`` ist unique).
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus, DocumentType, ReviewStatus


def unique_hash() -> str:
    """Kollisionsfreier Platzhalter fuer ``Document.file_hash``."""
    return uuid.uuid4().hex


def document_defaults() -> dict[str, Any]:
    """Pflichtfelder + typische Analyse-Werte eines archivierten Dokuments."""
    return dict(
        original_filename="test.pdf",
        stored_filename="abc_test.pdf",
        file_path="/archive/2024/03/RECHNUNG/abc_test.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        file_hash=unique_hash(),
        document_type=DocumentType.RECHNUNG,
        title="Testrechnung",
        issuer="Test GmbH",
        ocr_text="OCR Text der Testrechnung",
        ocr_confidence=0.9,
        ai_confidence=0.92,
        status=DocumentStatus.ACTIVE,
        review_status=ReviewStatus.OK,
    )


async def make_document(db: AsyncSession, *, commit: bool = False, **overrides: Any) -> Document:
    """Legt ein Dokument an (flush, optional commit) und gibt es zurueck.

    ``overrides`` ueberschreiben die Defaults aus :func:`document_defaults`.
    """
    doc = Document(**{**document_defaults(), **overrides})
    db.add(doc)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return doc
