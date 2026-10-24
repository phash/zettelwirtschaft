"""Schemas der Neu-Analyse-API (Aenderungsvorschlaege, Issue #39)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

# Deutsche Anzeigenamen der Vorschlagsfelder (Schluessel = PROPOSAL_FIELDS).
FIELD_LABELS: dict[str, str] = {
    "document_type": "Dokumenttyp",
    "title": "Titel",
    "issuer": "Aussteller",
    "document_date": "Datum",
    "amount": "Betrag",
    "currency": "Währung",
    "summary": "Zusammenfassung",
    "tax_relevant": "Steuerrelevant",
    "tax_category": "Steuerkategorie",
    "tax_year": "Steuerjahr",
    "recipient": "Empfänger",
    "reference_number": "Referenznummer",
    "filing_scope_id": "Ablagebereich",
    "tags": "Tags",
    "warranty": "Garantie",
    "ocr_text": "Erkannter Text",
}


class ReanalyzeRequest(BaseModel):
    """Body von ``POST /review/documents/{id}/reanalyze`` (optional)."""

    with_ocr: bool = False


class FieldChangeResponse(BaseModel):
    field: str
    label: str
    old: Any = None
    new: Any = None


class ProposalResponse(BaseModel):
    document_id: str
    document_title: str
    model: str
    with_ocr: bool
    ocr_note: str | None = None
    created_at: datetime
    changes: list[FieldChangeResponse]


class ProposalListResponse(BaseModel):
    items: list[ProposalResponse]
    total: int
    # Anzahl Vorschlaege je Feld (ohne reine Text-Felder Titel/Zusammenfassung).
    field_counts: dict[str, int]


class ApplyRequest(BaseModel):
    """``fields=None`` uebernimmt alle Aenderungen."""

    fields: list[str] | None = None


class BulkProposalRequest(BaseModel):
    """``field=None``: alle sichtbaren Vorschlaege; sonst nur die mit diesem Feld."""

    field: str | None = None
