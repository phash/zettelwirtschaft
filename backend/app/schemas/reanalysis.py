"""Schemas der Neu-Analyse-API (Aenderungsvorschlaege, Issue #39)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

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
    # Steuerjahr/Garantie bei geaendertem Datum: Wert, falls das Datum abgelehnt
    # wird ({"new": ...}), oder {"unchanged": true} (dann keine Aenderung).
    without_new_date: dict | None = None


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


# --- Massenlauf -----------------------------------------------------------------

MAX_RUN_DOCUMENTS = 5000


class RunRequest(BaseModel):
    """Auswahl eines Massenlaufs: ``document_ids`` (Haekchen) ODER ``outdated``
    ("alle, die nicht mit dem aktuellen Modell analysiert wurden")."""

    document_ids: list[str] | None = Field(default=None, max_length=MAX_RUN_DOCUMENTS)
    outdated: bool = False
    with_ocr: bool = False

    @model_validator(mode="after")
    def _one_selection(self) -> "RunRequest":
        if (self.document_ids is not None) == self.outdated:
            raise ValueError("Entweder Dokumente auswählen oder „nicht mit aktuellem Modell“ wählen.")
        return self


class RunPreviewResponse(BaseModel):
    eligible: int
    reviewed_excluded: int
    no_text_excluded: int
    deleted_or_missing: int
    estimate_seconds: int
    model: str  # OLLAMA_MODEL: "Alle, die nicht mit <model> analysiert wurden"


class RunFailure(BaseModel):
    document_id: str
    # None, wenn das Dokument nicht mehr existiert.
    title: str | None = None
    error: str | None = None


class RunResponse(BaseModel):
    id: str
    status: str
    model: str
    with_ocr: bool
    total: int
    done: int
    proposed: int
    unchanged: int
    failed: int
    skipped: int
    last_error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    # Hoechstens RUN_FAILURES_LIMIT fehlgeschlagene/uebersprungene Dokumente mit Grund.
    failures: list[RunFailure]
