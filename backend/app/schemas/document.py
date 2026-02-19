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
    answer: str | None = None
    field_affected: str | None = None
    is_answered: bool
    created_at: datetime
    answered_at: datetime | None = None


class ReviewQuestionAnswer(BaseModel):
    answer: str


def _extract_scope_name(data):
    """Extrahiert filing_scope_name aus ORM-Relationship."""
    if hasattr(data, "filing_scope") and data.filing_scope is not None:
        return data.filing_scope.name
    return None


class DocumentResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    original_filename: str
    stored_filename: str
    file_path: str
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

    @model_validator(mode="before")
    @classmethod
    def resolve_filing_scope(cls, data):
        if hasattr(data, "__dict__"):  # ORM object
            scope_name = _extract_scope_name(data)
            if scope_name:
                # Wrap in a dict-like proxy
                class _Proxy:
                    def __init__(self, obj, extra):
                        self._obj = obj
                        self._extra = extra
                    def __getattr__(self, name):
                        if name in self._extra:
                            return self._extra[name]
                        return getattr(self._obj, name)
                return _Proxy(data, {"filing_scope_name": scope_name})
        return data


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

    @model_validator(mode="before")
    @classmethod
    def resolve_filing_scope(cls, data):
        if hasattr(data, "__dict__"):  # ORM object
            scope_name = _extract_scope_name(data)
            if scope_name:
                class _Proxy:
                    def __init__(self, obj, extra):
                        self._obj = obj
                        self._extra = extra
                    def __getattr__(self, name):
                        if name in self._extra:
                            return self._extra[name]
                        return getattr(self._obj, name)
                return _Proxy(data, {"filing_scope_name": scope_name})
        return data


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
