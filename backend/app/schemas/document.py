from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.models.document import DocumentStatus, DocumentType, ReviewStatus, TaxCategory


# K-3 (Re-Review): Pydantic v2 serialisiert Decimal standardmaessig als String
# ("119.99"). Existierende API-Konsumenten (Frontend Tests, externe Clients)
# erwarten JSON-Numbers. Wir geben Decimal explizit als float zurueck, ohne
# die DB-Praezision zu opfern (Aggregation laeuft weiterhin als Decimal).
def _decimal_to_float(value):
    return float(value) if value is not None else None


_MISSING = object()


class TagResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    name: str
    category: str | None = None
    is_auto_generated: bool


class WarrantyInfoResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    product_name: str
    product_category: str | None = None
    purchase_date: date
    warranty_end_date: date
    warranty_type: str
    warranty_duration_months: int
    retailer: str | None = None
    is_expired: bool
    reminder_sent: bool
    notes: str | None = None


class ReviewQuestionResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    question: str
    question_type: str | None = None
    explanation: str | None = None
    answer: str | None = None
    field_affected: str | None = None
    suggested_answers: str | None = None
    is_answered: bool
    priority: int = 0
    created_at: datetime
    answered_at: datetime | None = None


def _resolve_scope_name(cls, data):
    """Model-Validator: extrahiert filing_scope_name aus ORM-Relationship.

    Frueher: gewrappt in einer _OrmProxy-Klasse, die jeden Attribut-Zugriff
    weiterreichen musste. Jetzt: kopieren in ein Dict + Scope-Name daraufsetzen.
    Pydantic v2 akzeptiert mit from_attributes=True auch dicts als Input.

    H-09: filing_scope-Zugriff defensiv per try/except — wenn das ORM-Doc
    ausserhalb der urspruenglichen Session validiert wird (z.B. nach refresh
    ohne explizites selectinload), wuerde Lazy-Loading einen MissingGreenlet
    ausloesen. Im Fehlerfall scope_name=None statt Crash.
    """
    # Wenn schon ein Dict (z.B. aus Tests / API-Round-Trip) — durchreichen.
    if isinstance(data, dict):
        return data
    # ORM-Objekt: Felder via getattr extrahieren. Fields ist der Schema-Bauplan
    # — wir lesen genau die Felder, die das Schema kennt.
    if hasattr(data, "__dict__"):
        fields = list(cls.model_fields.keys())
        out = {}
        for f in fields:
            if f == "filing_scope_name":
                try:
                    fs = getattr(data, "filing_scope", None)
                    out[f] = fs.name if fs is not None else None
                except Exception:
                    # MissingGreenlet / detached instance — gracefully degrade
                    out[f] = None
            else:
                # Kein hasattr(): das triggert selbst schon den Lazy-Load und
                # faengt nur AttributeError, nicht MissingGreenlet.
                try:
                    value = getattr(data, f, _MISSING)
                except Exception:
                    value = None
                if value is not _MISSING:
                    out[f] = value
        return out
    return data


class DocumentResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    original_filename: str
    # `stored_filename` wurde aus VULN-014-Gruenden entfernt: der UUID-prefixed
    # Disk-Name disclosed Innenstruktur des Archivs und half bei Path-Traversal.
    # Frontend nutzt `original_filename` fuer Anzeige + `/api/documents/{id}/file`
    # fuer Download; der interne Name ist nicht noetig.
    # Gleiches gilt fuer thumbnail_path (absoluter Server-Pfad, im Native-Modus
    # inkl. Windows-Benutzername) und file_hash — das Frontend laedt Thumbnails
    # ueber /api/documents/{id}/thumbnail.
    file_type: str
    file_size_bytes: int
    document_type: DocumentType
    title: str
    document_date: date | None = None
    amount: Decimal | None = None

    @field_serializer("amount")
    def _ser_amount(self, v):
        return _decimal_to_float(v)
    currency: str
    issuer: str | None = None
    recipient: str | None = None
    reference_number: str | None = None
    summary: str | None = None
    ocr_text: str
    ocr_confidence: float
    tax_relevant: bool
    tax_year: int | None = None
    tax_category: TaxCategory | None = None
    status: DocumentStatus
    review_status: ReviewStatus
    ai_confidence: float
    created_at: datetime
    updated_at: datetime
    scanned_at: datetime | None = None
    filing_scope_id: str | None = None
    filing_scope_name: str | None = None
    tags: list[TagResponse] = []
    warranty_info: WarrantyInfoResponse | None = None
    review_questions: list[ReviewQuestionResponse] = []
    # Herkunft aus einem aufgeteilten Stapel (Spezifikation 12). split_part_count,
    # split_source_filename und can_undo_split/split_undo_blocked_reason
    # ("Aufteilung rückgängig" moeglich bzw. warum nicht) setzt der Endpoint.
    split_batch_id: str | None = None
    split_part_index: int | None = None
    split_part_count: int | None = None
    split_source_filename: str | None = None
    source_pages: str | None = None
    can_undo_split: bool = False
    split_undo_blocked_reason: str | None = None
    # Modell der letzten Analyse/Neu-Analyse; has_proposal: offener
    # Aenderungsvorschlag mit wesentlichen Aenderungen (setzt der Endpoint).
    analysis_model: str | None = None
    has_proposal: bool = False

    resolve_filing_scope = model_validator(mode="before")(classmethod(_resolve_scope_name))


class DocumentListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    original_filename: str
    file_type: str
    file_size_bytes: int
    document_type: DocumentType
    title: str
    document_date: date | None = None
    amount: Decimal | None = None
    currency: str
    issuer: str | None = None
    tax_relevant: bool
    status: DocumentStatus
    review_status: ReviewStatus
    ai_confidence: float
    filing_scope_id: str | None = None
    filing_scope_name: str | None = None
    created_at: datetime
    tags: list[TagResponse] = []

    resolve_filing_scope = model_validator(mode="before")(classmethod(_resolve_scope_name))

    @field_serializer("amount")
    def _ser_amount(self, v):
        return _decimal_to_float(v)


class PaginatedDocumentsResponse(BaseModel):
    items: list[DocumentListItem]
    total: int
    page: int
    page_size: int


# Felder, die in der DB NOT NULL sind: explizites null wird abgelehnt (422)
# statt als IntegrityError (500) zu enden.
_UPDATE_REQUIRED_FIELDS = ("title", "document_type", "currency", "tax_relevant")


class DocumentUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    document_type: DocumentType | None = None
    document_date: date | None = None
    # Negative Betraege erlaubt (Gutschriften); Grenzen wie Numeric(12,2).
    amount: Decimal | None = Field(None, max_digits=12, decimal_places=2)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    issuer: str | None = Field(None, max_length=500)
    recipient: str | None = Field(None, max_length=500)
    reference_number: str | None = Field(None, max_length=200)
    summary: str | None = None
    tax_relevant: bool | None = None
    tax_year: int | None = Field(None, ge=1990, le=2100)
    tax_category: TaxCategory | None = None
    filing_scope_id: str | None = None

    @field_validator(
        "document_date", "amount", "issuer", "recipient", "reference_number",
        "summary", "tax_year", "tax_category", "filing_scope_id",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value):
        """Leeres Formularfeld ("") = Wert loeschen. Frueher endete ein geleertes
        Betrag-/Datumsfeld mit 422 — falsche Werte liessen sich nie entfernen."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("title", "issuer", "recipient", "reference_number", mode="before")
    @classmethod
    def _strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _reject_null_for_required(self):
        for field in _UPDATE_REQUIRED_FIELDS:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Feld '{field}' darf nicht leer sein")
        return self


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class DashboardStats(BaseModel):
    total_documents: int
    documents_this_month: int
    pending_reviews: int
    expiring_warranties_30d: int
    processing_jobs: int
