from datetime import date
from decimal import Decimal

from pydantic import BaseModel


# H-ARCH-2: Decimal statt float bei Geldbetraegen. Pydantic v2 serialisiert
# Decimal standardmaessig als JSON-String — Frontend formatAmount() macht
# parseFloat, daher kompatibel ohne Anpassung.


class TaxCategorySummary(BaseModel):
    category: str
    label: str
    document_count: int
    total_amount: Decimal


class TaxYearSummary(BaseModel):
    year: int
    total_documents: int
    total_amount: Decimal
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
    amount: Decimal | None = None
    currency: str = "EUR"
    issuer: str | None = None
    tax_category: str | None = None
    file_type: str
