import json
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.schemas.document import _decimal_to_float


class SearchResultItem(BaseModel):
    id: str
    title: str
    document_type: str
    document_date: date | None = None
    amount: Decimal | None = None
    currency: str = "EUR"
    issuer: str | None = None
    tags: list[str] = []
    tax_relevant: bool = False
    # Ausschnitt mit Treffern; Treffer sind von HIGHLIGHT_START/HIGHLIGHT_END
    # (STX/ETX, siehe search_service) umschlossen — kein HTML.
    highlight: str = ""
    relevance_score: float = 0.0
    ai_confidence: float = 0.0
    created_at: datetime

    @field_serializer("amount")
    def _ser_amount(self, v):
        return _decimal_to_float(v)


class ScopeFacet(BaseModel):
    id: str
    name: str
    count: int


class SearchFacets(BaseModel):
    document_types: dict[str, int] = {}
    years: dict[str, int] = {}
    top_issuers: dict[str, int] = {}
    filing_scopes: list[ScopeFacet] = []


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int
    page: int
    page_size: int
    facets: SearchFacets


class SuggestResponse(BaseModel):
    suggestions: list[str]


class SavedSearchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    query_params: dict


class SavedSearchResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    name: str
    query_params: dict | str
    created_at: datetime

    @field_validator("query_params", mode="before")
    @classmethod
    def parse_query_params(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return v
        return v
