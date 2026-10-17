from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_serializer


# H-ARCH-2: Decimal intern (Aggregation), float ueber JSON (API-Stabilitaet,
# Frontend-Kompatibilitaet — siehe K-3 Re-Review).


def _decimal_to_float(value):
    return float(value) if value is not None else None


class TaxCategorySummary(BaseModel):
    category: str
    label: str
    document_count: int
    # Summe nur der EUR-Belege (Fremdwaehrungen stehen in non_eur_documents)
    total_amount: Decimal

    @field_serializer("total_amount")
    def _ser_total(self, v):
        return _decimal_to_float(v)


class NonEurDocument(BaseModel):
    """Beleg in Fremdwaehrung — nicht in den EUR-Summen enthalten."""
    id: str
    title: str
    document_date: date | None = None
    amount: Decimal | None = None
    currency: str

    @field_serializer("amount")
    def _ser_amount(self, v):
        return _decimal_to_float(v)


class TaxYearSummary(BaseModel):
    year: int
    total_documents: int
    # Summe nur der EUR-Belege — Fremdwaehrungen werden nicht umgerechnet.
    total_amount: Decimal
    categories: list[TaxCategorySummary]
    non_eur_documents: list[NonEurDocument] = []
    warnings: list[str] = []

    @field_serializer("total_amount")
    def _ser_total(self, v):
        return _decimal_to_float(v)


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
