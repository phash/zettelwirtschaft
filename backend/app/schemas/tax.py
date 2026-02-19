from datetime import date

from pydantic import BaseModel


class TaxCategorySummary(BaseModel):
    category: str
    label: str
    document_count: int
    total_amount: float


class TaxYearSummary(BaseModel):
    year: int
    total_documents: int
    total_amount: float
    categories: list[TaxCategorySummary]
    warnings: list[str] = []


class TaxExportRequest(BaseModel):
    year: int
    include_overview_pdf: bool = True
    include_csv: bool = True
    filing_scope_id: str | None = None


class TaxExportValidation(BaseModel):
    year: int
    total_documents: int
    warnings: list[str]
    ready: bool


class TaxDocumentItem(BaseModel):
    id: str
    title: str
    document_type: str
    document_date: date | None = None
    amount: float | None = None
    currency: str = "EUR"
    issuer: str | None = None
    tax_category: str | None = None
    file_type: str
