from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, model_validator

from app.models.document import DocumentStatus, DocumentType, ReviewStatus, TaxCategory


# K-3 (Re-Review): Pydantic v2 serialisiert Decimal standardmaessig als String
# ("119.99"). Existierende API-Konsumenten (Frontend Tests, externe Clients)
# erwarten JSON-Numbers. Wir geben Decimal explizit als float zurueck, ohne
# die DB-Praezision zu opfern (Aggregation laeuft weiterhin als Decimal).
def _decimal_to_float(value):
    return float(value) if value is not None else None


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


class ReviewQuestionAnswer(BaseModel):
    answer: str


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
            elif hasattr(data, f):
                try:
                    out[f] = getattr(data, f)
                except Exception:
                    out[f] = None
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
    thumbnail_path: str | None = None
    file_type: str
    file_size_bytes: int
    file_hash: str
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
    thumbnail_path: str | None = None
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


class DocumentUpdate(BaseModel):
    title: str | None = None
    document_type: DocumentType | None = None
    document_date: date | None = None
    amount: Decimal | None = None
    currency: str | None = None
    issuer: str | None = None
    recipient: str | None = None
    reference_number: str | None = None
    summary: str | None = None
    tax_relevant: bool | None = None
    tax_year: int | None = None
    tax_category: TaxCategory | None = None
    filing_scope_id: str | None = None


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class DashboardStats(BaseModel):
    total_documents: int
    documents_this_month: int
    pending_reviews: int
    expiring_warranties_30d: int
    processing_jobs: int
