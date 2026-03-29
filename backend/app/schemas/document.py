from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.models.document import DocumentStatus, DocumentType, ReviewStatus, TaxCategory


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
    """Model-Validator: extrahiert filing_scope_name aus ORM-Relationship."""
    if hasattr(data, "__dict__") and hasattr(data, "filing_scope") and data.filing_scope is not None:
        scope_name = data.filing_scope.name

        class _OrmProxy:
            """Proxy um ORM-Objekt mit zusaetzlichem Attribut zu wrappen."""
            __slots__ = ("_obj", "_filing_scope_name")

            def __init__(self, obj, name):
                object.__setattr__(self, "_obj", obj)
                object.__setattr__(self, "_filing_scope_name", name)

            def __getattr__(self, name):
                if name == "filing_scope_name":
                    return object.__getattribute__(self, "_filing_scope_name")
                return getattr(object.__getattribute__(self, "_obj"), name)

        return _OrmProxy(data, scope_name)
    return data


class DocumentResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    original_filename: str
    stored_filename: str
    thumbnail_path: str | None = None
    file_type: str
    file_size_bytes: int
    file_hash: str
    document_type: DocumentType
    title: str
    document_date: date | None = None
    amount: float | None = None
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
    amount: float | None = None
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


class PaginatedDocumentsResponse(BaseModel):
    items: list[DocumentListItem]
    total: int
    page: int
    page_size: int


class DocumentUpdate(BaseModel):
    title: str | None = None
    document_type: DocumentType | None = None
    document_date: date | None = None
    amount: float | None = None
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
